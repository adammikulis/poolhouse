"""How a `Broker` lets a server go: the file of who holds what, the proof asked before any stop, and the stop itself."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from poolhouse.files import read_json, write_json
from poolhouse.lock import only_one
from poolhouse.serve import exit_guard, inuse
from poolhouse.serve.leases import recorded_servers

__all__ = ["BrokerStops"]


class BrokerStops:
    """The stopping half of a `Broker`, which supplies the state it works on."""

    if TYPE_CHECKING:
        _cond: threading.Condition
        _gone: set[str]
        servers: dict[int, Any]
        held_file: Path
        manager: Any
        alive: Callable[[int | None], bool]
        say: Callable[[str], Any]

        def _end(self, held: Any, lease: str, *, keep_record: bool = False) -> None: ...
        def _flush_ended(self) -> None: ...
        def _stop(self, held: Any, why: str, *, asker: str = "") -> None: ...
        @staticmethod
        def _held_said(held: Any) -> str: ...

    # ------------------------------------------------------------------ the file of holders
    def _write_held(self) -> None:
        """Record who holds what, so a broker that restarts does not unload a server its
        holder is still using. Holders this broker never knew, such as those another broker over the same records wrote,
        stay while their process lives. Called with the lock held."""
        self._gone -= {lease for held in self.servers.values() for lease in held.holders}   # an id taken again is held
        with only_one(self.held_file.with_suffix(".lock"), wait=True, timeout=30.0, announce=lambda _line: None):
            kept: dict[str, Any] = {
                str(held.port): {"purpose": held.purpose, "model": held.model,
                                 "holders": {lease: list(who) for lease, who in held.holders.items()},
                                 "why": dict(held.records)}
                for held in self.servers.values() if held.holders and not held.loading}
            for port, other in self._read_held().items():
                theirs = {lease: who for lease, who in (other.get("holders") or {}).items()
                          if lease not in self._gone and self.alive(int(who[0]))}
                if not theirs:
                    continue
                entry = kept.setdefault(str(port), {**other, "holders": {}, "why": {}})
                entry["holders"] = {**theirs, **entry["holders"]}
                entry["why"] = {**{k: v for k, v in (other.get("why") or {}).items() if k in theirs}, **entry["why"]}
            write_json(self.held_file, kept)

    def _read_held(self) -> dict[int, dict[str, Any]]:
        """What the last broker recorded, by port; whatever cannot be read is nothing."""
        kept = read_json(self.held_file, {})
        out: dict[int, dict[str, Any]] = {}
        for port, entry in (kept.items() if isinstance(kept, dict) else ()):
            if str(port).isdigit() and isinstance(entry, dict):
                out[int(port)] = entry
        return out

    # ------------------------------------------------------------------ letting go
    @staticmethod
    def _hand_to(holder: int, server: int | None) -> None:
        """A server another process holds does not end with this one: its holders and the lease record say who may stop it."""
        if holder != os.getpid():
            exit_guard.release(server)

    def _in_use(self, held: Any, *, asker: int = 0) -> str:
        """Why ``held`` counts as in use by any record or by a request on it (`inuse.why_in_use`); empty only when shown unused."""
        if not held.pid:
            return "its process is not known"
        entry = recorded_servers(self.manager.state_file).get(held.port) or {}
        asking = inuse.Asking(dict(held.holders), set(self._gone), asker, self.alive)
        return inuse.why_in_use(held.port, {**entry, "pid": held.pid}, self.manager.state_file, asking)

    def _keep(self, held: Any, why: str) -> None:
        """Take ``held``, which was about to be stopped and is in use, back among the servers."""
        with self._cond:
            held.idle_since = time.monotonic()
            self.servers.setdefault(held.port, held)
            self._write_held()
            self._cond.notify_all()
        self.say(f"port {held.port}: left running: {why}")

    def stop(self, port: int, *, force: bool = False) -> dict[str, Any]:
        """Stop the server on ``port``. Left running while anyone holds it or a request is on it, unless ``force``."""
        with self._cond:
            held = self.servers.get(port)
            if held is None:
                return {"stopped": False, "why": f"nothing the broker knows is on port {port}"}
            if not held.ours:
                return {"stopped": False, "why": f"port {port} was not started through poolhouse"}
            if (held.holders or held.loading) and not force:
                return {"stopped": False, "why": self._held_said(held)}
        if not force and (why := self._in_use(held)):
            return {"stopped": False, "why": f"port {port}: {why}"}
        with self._cond:
            if self.servers.get(port) is not held or ((held.holders or held.loading) and not force):
                return {"stopped": False, "why": f"port {port} was taken while it was being stopped"}
            cut = self._held_said(held) if held.holders else ""
            self.servers.pop(port)
            for lease in list(held.holders):
                self._end(held, lease)
            self._write_held()
            self._cond.notify_all()
        self._flush_ended()
        self._stop(held, f"asked to stop it, cutting off: {cut}" if cut else "asked to stop it and nothing uses it")
        return {"stopped": True, "port": port, "model": held.model}

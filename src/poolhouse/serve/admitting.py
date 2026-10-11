"""What `ServerManager` does before a process is started: reuse a server that already fits, wait for one that is loading, take the
memory and the slot for it from the node's lease table, stop what a dead process left running, and take in an unmanaged server
when the setting allows it.

The look at the records and the write of the pending record happen under one file lock beside the lease file, so two processes
asking at the same moment see each other's records; the memory is the node's to give, and a start that waits for it waits outside
the lock.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from poolhouse import leasetable
from poolhouse.client import is_healthy, reported_models
from poolhouse.client.health import serving_params
from poolhouse.lock import only_one
from poolhouse.node_health import Refused
from poolhouse.serve import admission, unmanaged
from poolhouse.serve.backend import Lease, ServerBackend, ServerInfo, ServerSpec
from poolhouse.serve.events import Event, emit
from poolhouse.serve.leases import merge_state, orphaned, recorded_servers
from poolhouse.serve.matching import model_matches, serving_mismatch
from poolhouse.serve.ports import DEFAULT_HOST
from poolhouse.serve.process import kill_process_tree

__all__ = ["STATE_LOCK_TIMEOUT_S", "Admitting"]

logger = logging.getLogger(__name__)

STATE_LOCK_TIMEOUT_S = 30.0
POLL_S = 0.25


class Admitting:
    """The admission half of `ServerManager`: it reads and writes the manager's records."""

    state_file: Path
    say: Callable[[str], None] | None
    confirm: Callable[[str], bool] | None
    table: leasetable.Table
    if TYPE_CHECKING:
        _mine: dict[str, dict]

        def _exclusive(self) -> AbstractContextManager[None]: ...
        def _load(self) -> dict: ...
        def _write(self, state: dict) -> None: ...
        def _save(self) -> None: ...
        def _pending(self, spec: ServerSpec, *, lease: str = "", est_mb: int = 0) -> Lease: ...
        def backend_for(self, spec: ServerSpec) -> ServerBackend: ...

    def _admitted(self, spec: ServerSpec, *, on_event: Event | None = None,
                  reuse: bool = False, load_s: float = 0.0) -> Lease | ServerInfo:
        """The record for a server about to start, written once the node has given it the memory and the slot; or, with ``reuse``,
        the running server that already serves ``spec``.

        Waits while a server that would serve ``spec`` is still loading (up to ``load_s``) and while the node holds the memory
        for the servers up; raises `AdmissionRefused` when it could never fit or no room comes free in `admission.wait_s()`.
        """
        told = self.say or logger.info
        began = time.monotonic()
        memory_until = began + admission.wait_s()
        wanted = admission.estimate_bytes(spec)
        asked: dict | None = None
        handed = False
        waiting = last = ""
        try:
            while True:
                with only_one(self.state_file.with_name("servers.start.lock"), wait=True,
                              timeout=STATE_LOCK_TIMEOUT_S, announce=logger.debug):
                    records = recorded_servers(self.state_file)
                    found, loading = (self._compatible(spec, records, on_event) if reuse
                                      else (None, False))
                    if found is not None:
                        return found
                    if not loading or time.monotonic() - began >= load_s:
                        asked = asked or admission.ask(self.table, spec, wanted, pid=os.getpid(),
                                                       ttl_s=int(load_s) + admission.KEEP_S)
                        if asked["state"] == "held":
                            handed = True
                            return self._pending(spec, lease=asked["id"], est_mb=-(-wanted // admission.MIB))
                        self._stop_leaked(records, told)
                        waiting = f"waiting for memory -- {admission.said(self.table, wanted)}"
                        if time.monotonic() >= memory_until:
                            raise admission.AdmissionRefused(
                                f"port {spec.port}: {admission.said(self.table, wanted)}; no memory came free in "
                                f"{admission.wait_s():.0f}s ({admission.ENV_WAIT} sets the wait)")
                    else:
                        waiting = "waiting for a server that serves it to finish loading"
                if waiting != last:
                    told(f"port {spec.port}: {waiting}")
                    last = waiting
                asked = self._polled(asked)
        finally:
            if asked is not None and not handed:
                with contextlib.suppress(OSError, Refused):
                    self.table.release(asked["id"])

    def _polled(self, asked: dict | None) -> dict | None:
        """The request for memory after a short wait on it, or after a short sleep when there is none; None when the node dropped it."""
        if asked is None:
            time.sleep(POLL_S)
            return None
        try:
            return self.table.wait(asked["id"], POLL_S)
        except Refused:
            return None

    def _bound(self, lease: str, pid: int | None) -> None:
        """Bind the node's lease of a server to the server's process, so the memory comes back when it ends."""
        if lease and pid:
            try:
                self.table.renew(lease, ttl_s=admission.KEEP_S, pid=pid)
            except (Refused, OSError) as why:
                logger.warning("the lease %s of the server (pid %s) was not bound to it: %s", lease, pid, why)

    def _given_back(self, entry: dict | None) -> None:
        """Give the node the lease an entry of the registry holds; the node ends it with the server's process anyway."""
        if entry and entry.get("lease"):
            with contextlib.suppress(Refused, OSError):
                self.table.release(str(entry["lease"]))

    def _stop_leaked(self, records: dict[int, dict], told: Callable[[str], None]) -> bool:
        """Stop every server whose leasing process has gone; whether any was."""
        stopped = False
        for port, entry in records.items():
            if orphaned(entry):
                told(f"port {port}: stopping the server (pid {entry['pid']}) left running "
                     f"by pid {entry['owner_pid']}, which has exited")
                kill_process_tree(int(entry["pid"]))
                stopped = True
        if stopped:
            self._save()
        return stopped

    def _compatible(self, spec: ServerSpec, records: dict[int, dict],
                    on_event: Event | None = None) -> tuple[ServerInfo | None, bool]:
        """``(a running server on another port that serves spec as asked, whether one that
        would is still loading)``."""
        loading = False
        for port, entry in records.items():
            if (port == spec.port or not admission.live(entry)
                    or not model_matches(str(entry.get("model") or ""), spec.model)):
                continue
            if entry.get("pending"):
                loading = loading or admission.pending_serves(spec, entry)
                continue
            base_url = f"http://{DEFAULT_HOST}:{port}"
            if not is_healthy(base_url, timeout=1.0):
                continue
            shape = replace(spec, port=port)
            mismatch = serving_mismatch(shape, reported_models(base_url), serving_params(base_url))
            if admission.compatible(shape, entry, mismatch):
                logger.info("reusing the server on %s for %s", base_url, spec.model)
                emit(on_event, "ready", port=port, adopted=True)
                return ServerInfo(base_url=base_url, port=port, pid=entry.get("pid"),
                                  backend=str(entry.get("backend") or ""), adopted=True), False
        return None, loading

    def _reusable(self, spec: ServerSpec, *, on_event: Event | None = None) -> ServerInfo | None:
        """A running server on another port that serves ``spec`` as asked, else ``None``."""
        return self._compatible(spec, recorded_servers(self.state_file), on_event)[0]

    def register_unmanaged(self, port: int, seen: unmanaged.Examined) -> None:
        """Put the examined server on ``port`` in the registry as one poolhouse does not own."""
        with self._exclusive():
            state = merge_state(self._load(), self._mine, os.getpid())
            state[str(port)] = unmanaged.adopt_entry(port, seen)
            self._write(state)

    def _unmanaged_on(self, spec: ServerSpec, told: Callable[[str], None]) -> ServerInfo | None:
        """The server poolhouse did not start that listens on ``spec.port``, taken into the
        registry when the adoption setting allows it and it passes every check; else
        ``None``."""
        how = unmanaged.mode()
        if how == "off":
            return None
        seen = unmanaged.examine(spec.port)
        if not seen.ok:
            told(f"port {spec.port}: not adopting the server there -- {seen.why}")
            return None
        base_url = f"http://{DEFAULT_HOST}:{spec.port}"
        mismatch = serving_mismatch(spec, reported_models(base_url), serving_params(base_url))
        if mismatch:
            told(f"port {spec.port}: not adopting pid {seen.pid} -- " + "; ".join(mismatch))
            return None
        what = f"pid {seen.pid} on port {spec.port} serving {Path(seen.model).name or '?'}"
        if how == "ask" and not (self.confirm is not None and self.confirm(what)):
            told(f"port {spec.port}: not adopting {what}; it was not confirmed")
            return None
        self.register_unmanaged(spec.port, seen)
        told(f"port {spec.port}: adopted {what} (setting {unmanaged.ENV}={how}); poolhouse "
             "queues its requests and counts its memory, and never stops it")
        logger.warning("adopted unmanaged server: pid=%s port=%s model=%s by pid %s",
                       seen.pid, spec.port, seen.model, os.getpid())
        return ServerInfo(base_url=base_url, port=spec.port, pid=seen.pid,
                          backend=self.backend_for(spec).name,
                          adopted=True)

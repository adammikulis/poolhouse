"""The model servers this process started: each one waited on however it ends, its exit status kept.

A child that exits and is not waited on stays in the process table as a defunct entry, and
everything that scans processes (busy-compute checks, the test harness, a person's ``ps``)
counts it as running compute. Every server a manager starts is held here by its ``Popen``;
stopping one ends its whole tree and waits for it, and `Children.sweep` collects the ones
that died on their own, so none is left defunct and none dies unreported.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

import psutil

from poolhouse.platform import end_child, exit_said
from poolhouse.serve.process import kill_process_tree, started_at

logger = logging.getLogger(__name__)

#: how many recent exits the status keeps; the pids of older ones are forgotten four times later
KEEP_EXITS = 50


@dataclass(frozen=True, slots=True)
class Exit:
    """How one server process ended: its port and pid, the exit status, and whether this
    process stopped it (``expected``) or it died on its own."""

    port: int
    pid: int
    code: int | None
    said: str
    expected: bool
    at: float


class Children:
    """The ``Popen`` of every server this process started, by pid, and how each ended."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: dict[int, tuple[int, Any]] = {}
        self._ended: dict[int, Exit] = {}
        self._untold: list[Exit] = []
        self._kept: set[int] = set()

    def hold(self, port: int, proc: Any) -> None:
        """Take responsibility for waiting on ``proc``, the server on ``port``."""
        with self._lock:
            self._held[proc.pid] = (port, proc)

    def detach(self, port: int) -> None:
        """Leave the server on ``port`` running when this process stops its servers. It is
        still waited on when it exits, so it does not end up defunct."""
        with self._lock:
            self._kept |= {pid for pid, (held_port, _) in self._held.items() if held_port == port}

    def ended(self, pid: int | None) -> bool:
        """Whether ``pid`` was one of ours that has been waited on; its number may be anyone's now."""
        with self._lock:
            return pid in self._ended

    def exits(self) -> list[dict[str, Any]]:
        """The most recent exits, oldest first, as the status reports them."""
        with self._lock:
            return [asdict(one) for one in list(self._ended.values())[-KEEP_EXITS:]]

    def _record(self, port: int, proc: Any, *, expected: bool) -> Exit:
        one = Exit(port=port, pid=proc.pid, code=proc.returncode, said=exit_said(proc.returncode),
                   expected=expected, at=time.time())
        with self._lock:
            self._ended[proc.pid] = one
            while len(self._ended) > KEEP_EXITS * 4:
                del self._ended[next(iter(self._ended))]
            if not expected:
                self._untold.append(one)
        return one

    def sweep(self) -> list[Exit]:
        """Wait on every held server that has already exited, and return those exits. A server
        found dead here was not stopped by us: each is logged, and `take_untold` hands it on once."""
        with self._lock:
            held = list(self._held.items())
        found = []
        for pid, (port, proc) in held:
            if proc.poll() is None:
                continue
            with self._lock:
                if self._held.pop(pid, None) is None:
                    continue
                self._kept.discard(pid)
            one = self._record(port, proc, expected=False)
            logger.warning("the server on port %s (pid %s) died on its own: %s", port, pid, one.said)
            found.append(one)
        return found

    def take_untold(self) -> list[Exit]:
        """The unexpected exits nobody has been told of yet, each returned once."""
        with self._lock:
            told, self._untold = self._untold, []
        return told

    def stop(self, port: int, *, proc: Any = None, grace_s: float = 5.0) -> list[Exit]:
        """End the server on ``port`` (and ``proc``, when it is not yet held), its descendants
        with it, and wait for each, so none stays defunct. Returns how each ended."""
        with self._lock:
            mine = [(pid, held) for pid, (held_port, held) in self._held.items() if held_port == port]
            for pid, _ in mine:
                del self._held[pid]
                self._kept.discard(pid)
            known = {pid for pid, _ in mine} | set(self._ended)
        victims = [held for _, held in mine]
        if proc is not None and proc.pid not in known:
            victims.append(proc)
        out = []
        for victim in victims:
            _end_tree(victim, grace_s)
            out.append(self._record(port, victim, expected=True))
        return out

    def end(self, port: int, pid: int | None, *, proc: Any = None, grace_s: float = 5.0) -> list[Exit]:
        """`stop`, and for a server that is not our child (adopted, or recorded by another
        process) the tree under ``pid`` ended by its number. A pid of ours that was waited on
        is never signalled: that number may belong to another process now."""
        out = self.stop(port, proc=proc, grace_s=grace_s)
        if not out and pid and not self.ended(pid):
            kill_process_tree(pid, grace_s=grace_s)
        return out

    def stop_all(self, *, grace_s: float = 5.0) -> list[Exit]:
        """End and wait for every server still held."""
        with self._lock:
            ports = {port for pid, (port, _) in self._held.items() if pid not in self._kept}
        return [one for port in sorted(ports) for one in self.stop(port, grace_s=grace_s)]


def _end_tree(proc: Any, grace_s: float) -> None:
    """Terminate everything under ``proc`` and ``proc`` itself, kill what outlasts the grace,
    and wait on ``proc``. The waiting is the ``Popen``'s, so its exit status is the real one."""
    if proc.poll() is None:
        birth = started_at(proc.pid)
        if birth is not None:
            with contextlib.suppress(ProcessLookupError, psutil.Error):
                kill_process_tree(proc.pid, grace_s=grace_s, reap_parent=False, expect_start=birth)
    end_child(proc, grace_s=grace_s)

"""Keeps a process a run starts from running its program until the host has been told which process it is.

The host cannot learn the pid of a new process before ``Popen`` returns, which is after the program has
started, so a program that forks a child, waits for it and executes another program could do all of it
before the host looks. Here the new process sends its pid and waits, before it executes anything, and
the host releases it once ``follow`` has returned.
"""

from __future__ import annotations

import contextlib
import os
import select
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from poolhouse.sandbox.backend import SandboxUnavailable

__all__ = ["Hold"]

HOLD_S = 10.0
"""Seconds a new process waits to be released before it gives up."""
PID_BYTES = 4
GO = b"g"
STOP = b"q"


class Hold:
    """The pipes between a new process and the host: ``wait`` runs in the new process (as its
    ``preexec_fn``); a thread of the host, started with the hold, hands the pid to ``follow`` and releases the
    process; ``finish`` ends the thread and closes the pipes once the process has started or failed to.
    ``error`` is what ``follow`` raised, if it did, once ``finish`` has returned; the process is then
    stopped before it runs anything."""

    def __init__(self, follow: Callable[[int], None]) -> None:
        self._follow = follow
        self._pid, self._release = self._pipes()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sandbox-hold")
        submitted = False
        try:
            self._served = self._pool.submit(self._serve)
            submitted = True
        finally:
            if not submitted:
                self._pool.shutdown(wait=False)
                self._close(*self._pid, *self._release)

    @staticmethod
    def _pipes() -> tuple[tuple[int, int], tuple[int, int]]:
        """The two pipes of a hold; `SandboxUnavailable` with none left open when the system cannot make both."""
        made: list[tuple[int, int]] = []
        try:
            for _ in range(2):
                made.append(os.pipe())
        except OSError as exc:
            for ends in made:
                Hold._close(*ends)
            raise SandboxUnavailable(f"the process cannot be held until the host has followed it: {exc}") from exc
        return made[0], made[1]

    @property
    def error(self) -> BaseException | None:
        """What ``follow`` raised; read it after `finish`."""
        return self._served.exception()

    def wait(self) -> None:
        """Tell the host this process's pid and wait to be released; raises when the host cannot follow it or does not answer."""
        os.write(self._pid[1], os.getpid().to_bytes(PID_BYTES, "little"))
        ready, _, _ = select.select([self._release[0]], [], [], HOLD_S)
        if not ready:
            raise TimeoutError("the host did not follow this process in time")
        if os.read(self._release[0], 1) != GO:
            raise OSError("the host cannot follow this process")

    def finish(self) -> None:
        """End the host's thread and close the pipes."""
        self._close(self._pid[1], self._release[0])
        self._pool.shutdown(wait=True)
        self._close(self._pid[0], self._release[1])

    @staticmethod
    def _close(*fds: int) -> None:
        for fd in fds:
            os.close(fd)

    def _serve(self) -> None:
        raw = os.read(self._pid[0], PID_BYTES)
        if len(raw) < PID_BYTES:
            return
        released = STOP
        try:
            self._follow(int.from_bytes(raw, "little"))
            released = GO
        finally:
            with contextlib.suppress(OSError):
                os.write(self._release[1], released)

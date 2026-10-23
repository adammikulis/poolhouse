"""Waiting for a process this one just started, and failing the moment it cannot come up.

A caller that starts a daemon and then polls for its record, port or health answer calls `wait`.
It polls the caller's ``ready``, the child itself and the failure record the child leaves
(`Failing`, `failure_in`), so a startup that raises and a child that is killed both end the wait
at once, with the cause in plain words.
"""

from __future__ import annotations

import contextlib
import os
import signal
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from poolhouse import errors, jobs
from poolhouse.files import read_json, versioned, write_json

__all__ = ["Child", "Failing", "StartFailed", "Watch", "child_of", "explain", "failure_in", "wait"]

FAILURE_VERSION = 1
UNKNOWN_EXIT = 1 << 30
"""What `Child.poll` says for a process that is gone when the way it ended was not seen."""
TRACEBACK_CHARS = 2000


class StartFailed(errors.Error):
    """The process did not come up. ``kind`` is ``crashed`` (it left a failure record), ``exited``
    (it ended without registering) or ``timeout`` (it is still running and has not answered)."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


class Child(Protocol):
    """A process this one started: ``poll`` is None while it runs, its exit code once it has ended."""

    def poll(self) -> int | None: ...


class _Pid:
    """A started process known only by its pid: how it ended is seen when it is this process's own child (the status is collected),
    and not seen when it is somebody else's."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.code: int | None = None

    def poll(self) -> int | None:
        if self.code is None:
            self.code = self._collected()
        return self.code

    def _collected(self) -> int | None:
        try:
            collected, status = os.waitpid(self.pid, os.WNOHANG)
        except (OSError, ValueError):
            return UNKNOWN_EXIT if jobs.ended(self.pid) else None
        if collected:
            return os.waitstatus_to_exitcode(status)
        return UNKNOWN_EXIT if jobs.ended(self.pid) else None


def child_of(ran: jobs.Detached | int) -> Child:
    """The process `jobs.detach` started (its own handle when it kept one), or a pid."""
    if isinstance(ran, int):
        return _Pid(ran)
    return ran.process if ran.process is not None else _Pid(ran.pid)


def exit_words(code: int) -> str:
    """How a process ended, in words: ``exited with status 3`` or ``was killed by SIGKILL``."""
    if code == UNKNOWN_EXIT:
        return "ended (how it ended was not seen)"
    if code < 0:
        try:
            return f"was killed by {signal.Signals(-code).name}"
        except ValueError:
            return f"was killed by signal {-code}"
    return f"exited with status {code}"


def _tail(log: Path | None, lines: int = 4) -> str:
    if log is None:
        return ""
    try:
        text = Path(log).read_text(encoding="utf-8", errors="replace")[-4000:]
    except OSError:
        return ""
    kept = [line.strip() for line in text.splitlines() if line.strip()][-lines:]
    return " | ".join(kept)[-500:]


def explain(code: int, *, what: str, log: Path | None = None) -> str:
    """Why ``what`` is not coming up when its process ended with ``code``."""
    said = f"{what} stopped while starting: it {exit_words(code)}"
    tail = _tail(log)
    return said + (f". Its log {log} ends: {tail}" if tail else (f"; see {log}" if log else ""))


def failure_in(path: Path, since: float, *, log: Path | None = None) -> str:
    """The cause a process recorded at ``path`` after ``since`` (epoch seconds), else ``""``."""
    held = read_json(Path(path), {})
    if not isinstance(held, dict) or not held or float(held.get("time") or 0.0) < since:
        return ""
    return f"{held.get('type', 'an error')}: {held.get('cause', '')}" + (f" (log: {log})" if log else "")


class Failing:
    """Wrap a daemon's startup: when it raises, leave the cause at ``path`` for the process
    waiting on it (`failure_in`) and let the error go on; when it is done, `started` clears an
    old record. A deliberate exit (`SystemExit`, `KeyboardInterrupt`) and the errors in ``ignore``
    are not failures."""

    def __init__(self, path: Path, ignore: tuple[type[BaseException], ...] = ()) -> None:
        self.path = Path(path)
        self.ignore = (KeyboardInterrupt, SystemExit, *ignore)
        self.done = False

    def __enter__(self) -> Failing:
        return self

    def started(self) -> None:
        """The daemon is up: an old failure record no longer describes it."""
        self.done = True
        self.path.unlink(missing_ok=True)

    def __exit__(self, kind, exc, tb) -> bool:
        if exc is None or self.done or isinstance(exc, self.ignore):
            return False
        text = "".join(traceback.format_exception(kind, exc, tb))[-TRACEBACK_CHARS:]
        # A state directory that cannot be written leaves the log and the exit status to say it.
        with contextlib.suppress(OSError):
            write_json(self.path, versioned({"pid": os.getpid(), "time": time.time(),
                                             "type": type(exc).__name__, "cause": str(exc),
                                             "traceback": text}, FAILURE_VERSION))
        return False


@dataclass(frozen=True)
class Watch:
    """What `wait` watches besides the child: the log its output goes to (named in the failure),
    ``failure`` (the cause the child recorded, or ``""``), how often to look, and whether a
    child that exits with status 0 has failed (not when it is a launcher handing over)."""

    log: Path | None = None
    failure: Callable[[], str] | None = None
    poll_s: float = 0.1
    clean_exit_ends: bool = True


def wait[T](child: Child, ready: Callable[[], T | None], *, what: str, timeout_s: float,
            watch: Watch = Watch()) -> T:  # noqa: B008 - frozen
    """The value ``ready`` returns once it is not None; `StartFailed` as soon as ``child`` ends
    without it, ``watch.failure`` names a cause, or ``timeout_s`` passes.

    A child that ends after registering is a success: ``ready`` is asked once more after the
    child is seen gone, so a second broker that quits because one runs does not fail the wait.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if (found := ready()) is not None:
            return found
        if watch.failure is not None and (why := watch.failure()):
            raise StartFailed(f"{what} stopped while starting: {why}", "crashed")
        code = child.poll()
        if code is not None and (code != 0 or watch.clean_exit_ends):
            if (found := ready()) is not None:
                return found
            if watch.failure is not None and (why := watch.failure()):
                raise StartFailed(f"{what} stopped while starting: {why}", "crashed")
            raise StartFailed(explain(code, what=what, log=watch.log), "exited")
        if time.monotonic() >= deadline:
            if code is not None:
                raise StartFailed(explain(code, what=what, log=watch.log), "exited")
            raise StartFailed(f"{what} is still running but did not come up within {timeout_s:.0f}s"
                              + (f"; see {watch.log}" if watch.log else ""), "timeout")
        time.sleep(watch.poll_s)

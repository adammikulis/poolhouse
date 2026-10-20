"""A daemon a test starts is stopped, with its children, when the test ends.

`poolhouse.jobs.detach` runs a child in its own session, so nothing ends it when the test that asked for it does. The fixture
records every ``poolhouse.cli.daemon`` the test starts through it and stops the process and the group it leads at teardown. The
fixture is imported by ``conftest.py`` and applies to every test.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Iterator
from typing import Any

import psutil
import pytest

DAEMON = "poolhouse.cli.daemon"
GRACE_S = 5.0


class _Spawns:
    """`subprocess` as `poolhouse.jobs` sees it, whose ``Popen`` notes each daemon started."""

    def __init__(self, started: list[int]) -> None:
        self._started = started

    def __getattr__(self, name: str) -> Any:
        return getattr(subprocess, name)

    def Popen(self, command: list[str], *args: Any, **kwargs: Any) -> subprocess.Popen:
        child = subprocess.Popen(command, *args, **kwargs)
        if DAEMON in command:
            self._started.append(child.pid)
        return child


def stop(pid: int) -> None:
    """End the process ``pid`` and everything below it and in the group it leads: SIGTERM, then SIGKILL for what is left after `GRACE_S`."""
    try:
        parent = psutil.Process(pid)
        doomed = [parent, *parent.children(recursive=True)]
    except psutil.Error:
        return
    with contextlib.suppress(OSError):
        os.killpg(pid, signal.SIGTERM)
    for process in doomed:
        with contextlib.suppress(psutil.Error):
            process.terminate()
    _, alive = psutil.wait_procs(doomed, timeout=GRACE_S)
    for process in alive:
        with contextlib.suppress(psutil.Error):
            process.kill()
    psutil.wait_procs(alive, timeout=GRACE_S)


@contextlib.contextmanager
def watching() -> Iterator[list[int]]:
    """The pids of the daemons started through `poolhouse.jobs.detach` inside the block; all are stopped when it ends."""
    from poolhouse import jobs

    started: list[int] = []
    real = jobs.subprocess
    jobs.subprocess = _Spawns(started)  # type: ignore[assignment]
    try:
        yield started
    finally:
        jobs.subprocess = real
        for pid in started:
            stop(pid)


@pytest.fixture(autouse=True)
def daemons_stop_with_the_test() -> Iterator[None]:
    """Run the test, then stop every daemon it started."""
    with watching():
        yield

"""Keeping the computer awake for a job: from sleeping when idle while a step runs, and, on mains power, while a job due within
`SOON_S` waits for its time. It ends with the job's process, changes no setting and cannot wake a computer that sleeps; a lid
closed on battery still sleeps, and the job then reports the time it missed.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import sys
from pathlib import Path

from poolhouse.platform import end_child, start_grouped

__all__ = ["SOON_S", "Awake"]

SOON_S = 12 * 3600
CAFFEINATE = "/usr/bin/caffeinate"
CONTINUOUS, SYSTEM = 0x80000000, 0x00000001


class Awake:
    """What this process holds to keep the computer awake: ``""``, ``waiting`` or ``running``."""

    def __init__(self) -> None:
        self.how = ""
        self._child: subprocess.Popen | None = None

    def hold(self, how: str) -> None:
        if how == self.how:
            return
        self.release()
        self.how = how
        if not how:
            return
        if sys.platform == "darwin" and Path(CAFFEINATE).exists():
            flag = "-i" if how == "running" else "-s"
            with contextlib.suppress(OSError):
                self._child = start_grouped([CAFFEINATE, flag, "-w", str(os.getpid())], stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sys.platform == "win32" and how == "running":
            ctypes.windll.kernel32.SetThreadExecutionState(CONTINUOUS | SYSTEM)

    def release(self) -> None:
        if self._child is not None:
            with contextlib.suppress(OSError):
                end_child(self._child)
            self._child = None
        elif sys.platform == "win32" and self.how == "running":
            ctypes.windll.kernel32.SetThreadExecutionState(CONTINUOUS)
        self.how = ""

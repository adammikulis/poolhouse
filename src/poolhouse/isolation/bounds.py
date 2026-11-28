"""The resource bounds of one confinement on macOS (G11; docs/design-isolated-launcher.md 5.2 and 17, docs/limits.md).

Three are told to the kernel at start (`sandbox.Limits`: CPU seconds, file size and descriptors, the rlimits of every process of the
confinement). The parent watches those, and memory and disk, which the kernel does not bound: `Watch` looks at the processes the follower names
and at the two writable trees, and the first bound passed ends every process of the session at once, with a reason in the parent's words. Each
number is the machine's, read at launch, or an existing constant of the project. Nothing here asks or waits for a person.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import shutil
import signal
import stat
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

import psutil

from poolhouse import leasetable, node_health
from poolhouse.isolation.serving import IDLE_SECONDS
from poolhouse.sandbox import Limits, process_tree
from poolhouse.sandbox.run import fair_share, live
from poolhouse.serve import node_memory
from poolhouse.serve._watchdog import POLL_S

__all__ = ["COPIES", "PASSED", "Bounds", "Watch", "derive"]

COPIES = 5
"""The full copies of a project the launcher's workspace holds (master, before, stage, result and current capture), as design 17 lists them."""
PASSED = "A resource bound ended the session:"


@dataclass(frozen=True, slots=True)
class Bounds:
    """What one confinement may take. ``cpu_seconds`` is for each process, ``file_bytes`` for each file, ``open_files`` for each process by the
    kernel and for the session's processes together by the watcher, ``memory_bytes`` for the session's processes together and ``disk_bytes`` for
    the stage and the agent home together. The watcher looks every ``interval_s`` seconds."""

    cpu_seconds: int
    file_bytes: int
    open_files: int
    memory_bytes: int
    disk_bytes: int
    interval_s: float = POLL_S

    def limits(self) -> Limits:
        """What the kernel is told for the processes of the session, with the session's share of the user's processes."""
        return Limits(wall_seconds=None, output_bytes=None, processes=fair_share(), cpu_seconds=self.cpu_seconds, file_bytes=self.file_bytes,
                      open_files=self.open_files)


def _sysctl(name: str) -> int:
    value, size = ctypes.c_int64(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int64))
    if ctypes.CDLL(None, use_errno=True).sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), f"the system value {name} cannot be read")
    return value.value


def _unadmitted() -> int:
    """The memory the broker reports unadmitted: what no lease holds of the node's memory budget. Where this machine's node keeps no budget, or
    does not answer, the memory the machine reports available."""
    table = leasetable.default()
    if (table.state / leasetable.CONFIG).is_file():   # no budget was ever written: there is no node to ask, and none is started for this
        with contextlib.suppress(OSError, node_health.Refused):
            free = node_memory.free_bytes(table)
            if free is not None:
                return free
    return psutil.virtual_memory().available


def derive(volume: str) -> Bounds | None:
    """The bounds of a confinement starting now whose stage is on the volume of ``volume``: each the machine's figure divided by the live
    confinements (this one counted) plus one for the person. ``None`` off macOS, where this watcher and these figures are not built."""
    if sys.platform != "darwin":
        return None
    import resource

    sharers = live() + 2

    def within(which: int, value: int) -> int:
        hard = resource.getrlimit(which)[1]
        return max(1, value if hard == resource.RLIM_INFINITY else min(value, hard))

    disk = max(1, shutil.disk_usage(volume).free // (COPIES * sharers))
    table = (_sysctl("kern.maxfiles") - _sysctl("kern.num_files")) // sharers
    return Bounds(within(resource.RLIMIT_CPU, int(IDLE_SECONDS) * (os.cpu_count() or 1) // sharers),
                  within(resource.RLIMIT_FSIZE, disk),
                  within(resource.RLIMIT_NOFILE, min(_sysctl("kern.maxfilesperproc"), table)),
                  _unadmitted() // sharers, disk)


def _held(roots: Sequence[str]) -> tuple[int, int]:
    """The bytes the files under ``roots`` hold (allocated blocks, each file once) and the size of the largest file; no link is followed. A
    directory that cannot be listed raises: what it holds cannot be bounded."""
    used = largest = 0
    seen: set[tuple[int, int]] = set()
    waiting = list(roots)
    while waiting:
        with contextlib.suppress(FileNotFoundError, NotADirectoryError), os.scandir(waiting.pop()) as entries:
            for entry in entries:
                try:
                    found = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISDIR(found.st_mode):
                    waiting.append(entry.path)
                if (found.st_dev, found.st_ino) not in seen:
                    seen.add((found.st_dev, found.st_ino))
                    used += found.st_blocks * 512
                    largest = max(largest, found.st_size if stat.S_ISREG(found.st_mode) else 0)
    return used, largest


class Watch:
    """The parent's watcher of one confinement over ``roots``, its writable trees. ``reason`` is empty until a bound is passed."""

    def __init__(self, bounds: Bounds, roots: Sequence[str]) -> None:
        self.bounds, self.roots = bounds, tuple(roots)
        self.reason = ""
        self._tree: process_tree.Tree | None = None
        self._stalled = 0.0
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, tree: process_tree.Tree, stalled: float) -> None:
        """Watch the processes of ``tree`` from now; ``stalled`` is what ends the wait for the tree to end (`process_tree.end`)."""
        self._tree, self._stalled = tree, stalled
        self._thread = threading.Thread(target=self._run, daemon=True, name="confinement-bounds")
        self._thread.start()

    def check(self) -> str:
        """Look now. The first bound found passed ends every process of the session and is the reason from then on; empty while none is. A
        look that cannot be taken ends the session too, and says so."""
        with self._lock:
            if self.reason or self._tree is None:
                return self.reason
            try:
                found = self._look(self._tree)
            except (OSError, ValueError) as error:
                found = f"its resource use could not be read ({type(error).__name__}: {error})."
            if found:
                self.reason = f"{PASSED} {found}"
                process_tree.end(self._tree, stalled=self._stalled)
            return self.reason

    def stop(self) -> str:
        """Stop watching, after one last look; the reason, if a bound was passed. Call it once the processes have ended and before the tree is
        released."""
        self._done.set()
        if self._thread is not None:
            self._thread.join()
        reason = self.check()
        self._tree = None
        return reason

    def fits(self, root: str) -> bool:
        """Whether what ``root`` holds is within the disk bound and each of its files under the file bound; False when that cannot be read."""
        try:
            used, largest = _held((root,))
        except (OSError, ValueError):
            return False
        return used <= self.bounds.disk_bytes and largest < self.bounds.file_bytes

    def _run(self) -> None:
        pause = self.bounds.interval_s
        while not self._done.wait(pause):
            began = time.monotonic()
            if self.check():
                return
            pause = max(self.bounds.interval_s, time.monotonic() - began)   # a look that takes longer than the interval is not run back to back

    def _look(self, tree: process_tree.Tree) -> str:
        bounds = self.bounds
        members = process_tree.usage(tree)
        cpu = f"a process used its CPU time bound of {bounds.cpu_seconds} seconds."
        size = f"a file reached the size bound of {bounds.file_bytes} bytes."
        for signum, said in ((getattr(signal, "SIGXCPU", 0), cpu), (getattr(signal, "SIGXFSZ", 0), size)):
            if signum in tree.killed_by:   # the kernel ended a process at a limit, perhaps one this watcher never saw alive
                return said
        memory = descriptors = 0
        for pid, footprint in members:
            try:
                process = psutil.Process(pid)
                times, opened = process.cpu_times(), process.num_fds()
            except psutil.Error:   # it ended between the list and the look, or it is not this user's to read
                continue
            if times.user + times.system >= bounds.cpu_seconds:   # a process that ignores the kernel's signal runs on: the parent ends it
                return cpu
            memory, descriptors = memory + footprint, descriptors + opened
        if memory > bounds.memory_bytes:
            return f"its processes held {memory} bytes of memory, past the bound of {bounds.memory_bytes} bytes."
        if descriptors >= bounds.open_files:
            return f"its processes held {descriptors} open descriptors, and the bound is {bounds.open_files}."
        used, largest = _held(self.roots)
        if largest >= bounds.file_bytes:   # the kernel writes up to the limit and no further, whether or not the signal is ignored
            return size
        if used > bounds.disk_bytes:
            return f"the working copy and the session's home held {used} bytes, past the disk bound of {bounds.disk_bytes} bytes."
        return ""

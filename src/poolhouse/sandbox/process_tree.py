"""The processes one confined run has started, followed from the moment the run began.

macOS tells a process that it forked (``kqueue``, ``NOTE_FORK``) without saying which child it made, so
each notification is answered by listing the children of that process (``proc_listpids`` of libproc)
and following them in turn. A child that has ended by the time its parent's notification is read is
not listed, so a tree counts the forks it was told of: a tree in which nothing forked holds the one
process it started and nothing else (`Tree.sealed`). Where there is no kqueue or libproc, and for a
process that had already ended when it was first looked at, the tree is not complete.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import select
import threading
import time
from collections.abc import Iterable

__all__ = ["Tree", "flush", "follow", "release"]

logger = logging.getLogger("poolhouse.sandbox")

PPID_ONLY = 6
LISTED = 4096
"""Children listed at once; a process with more makes its tree not complete."""
KEPT = 65536
"""Processes one tree keeps; a run that starts more is not followed past them and its tree is not complete."""
GRACE_S = 10.0
"""Seconds after a process ended that its pid still counts as the tree's: the kernel's report of what it
refused can arrive after the process ended, and a pid is not given to another process at once."""
WATCHED = (getattr(select, "KQ_NOTE_FORK", 0) | getattr(select, "KQ_NOTE_EXIT", 0))
RUSAGE_V2 = 2
RUSAGE_WORDS = 64
CHILD_WORDS = range(12, 18)
"""The words of ``rusage_info_v2`` that count the children a process has waited for (``ri_child_*``)."""


class Tree:
    """The processes of one run: the process it started and every descendant seen starting.
    ``complete`` is False when a process could not be followed from its start and ``forks`` counts the
    processes of the tree that forked (at least one for any fork; a count of notifications, not of children)."""

    def __init__(self, root: int) -> None:
        self.root = root
        self.complete = True
        self.forks = 0
        self._lock = threading.Lock()
        self._seen: dict[int, float | None] = {root: None}

    @property
    def sealed(self) -> bool:
        """Whether the tree is the one process it started: it was followed from the start and nothing in it forked."""
        return self.complete and not self.forks

    def __contains__(self, pid: object) -> bool:
        with self._lock:
            return self._held(pid, time.monotonic())

    def holds_any(self, pids: Iterable[int]) -> bool:
        """Whether any of ``pids`` is in the tree."""
        now = time.monotonic()
        with self._lock:
            return any(self._held(pid, now) for pid in pids)

    def add(self, pid: int) -> bool:
        """Put ``pid`` in the tree, again when an earlier process had it; False when the tree is full."""
        with self._lock:
            if pid not in self._seen and len(self._seen) >= KEPT:
                self._expire(time.monotonic())
                if len(self._seen) >= KEPT:
                    self.complete = False
                    return False
            self._seen[pid] = None
            return True

    def ended(self, pid: int) -> None:
        """Note that the process ``pid`` has ended; its pid stays the tree's for `GRACE_S`."""
        with self._lock:
            if pid in self._seen:
                self._seen[pid] = time.monotonic()

    def forked(self) -> None:
        """Note that a process of the tree forked."""
        with self._lock:
            self.forks += 1

    def _held(self, pid: object, now: float) -> bool:
        if pid not in self._seen:
            return False
        ended = self._seen[pid]
        return ended is None or now - ended <= GRACE_S

    def _expire(self, now: float) -> None:
        for pid in [pid for pid, ended in self._seen.items() if ended is not None and now - ended > GRACE_S]:
            del self._seen[pid]


class _Follower:
    """One kqueue, and a thread that reads it, for every tree followed in this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._drain = threading.Lock()
        self._owners: dict[int, Tree] = {}
        self._queue: select.kqueue | None = None
        self._libproc: ctypes.CDLL | None = None
        self._ready = False

    def follow(self, root: int) -> Tree:
        """A `Tree` that grows with the descendants of ``root`` from now on."""
        tree = Tree(root)
        if not self._start():
            tree.complete = False
            return tree
        if not self._register(root, tree):
            tree.complete = False
            return tree
        if self._reaped_children(root):
            tree.forked()
        if not self._scan(root, tree):
            tree.complete = False
        return tree

    def release(self, tree: Tree) -> None:
        """Stop following the processes of ``tree``."""
        with self._lock:
            mine = [pid for pid, owner in self._owners.items() if owner is tree]
            for pid in mine:
                del self._owners[pid]
            queue = self._queue
        for pid in mine:
            with contextlib.suppress(OSError, ValueError):
                queue.control([select.kevent(pid, select.KQ_FILTER_PROC, select.KQ_EV_DELETE)], 0, 0)

    def flush(self) -> None:
        """Take in every notification the kernel has queued, so that a fork or an exit that happened before the call is counted."""
        queue = self._queue
        if queue is None or not self._ready:
            return
        try:
            with self._drain:
                while events := queue.control(None, 64, 0):
                    for event in events:
                        self._handle(event)
        except (OSError, ValueError, ctypes.ArgumentError):
            if queue is self._queue:
                logger.exception("the processes of sandboxed runs are no longer followed")
                self._stop()

    def _start(self) -> bool:
        with self._lock:
            if self._ready:
                return True
            if not hasattr(select, "kqueue"):
                return False
            try:
                libproc = ctypes.CDLL("libproc.dylib", use_errno=True)
                libproc.proc_listpids.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int]
                libproc.proc_listpids.restype = ctypes.c_int
                libproc.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
                libproc.proc_pid_rusage.restype = ctypes.c_int
                self._queue = select.kqueue()
            except (OSError, AttributeError) as exc:
                logger.warning("the processes of a sandboxed run cannot be followed: %s", exc)
                return False
            self._libproc = libproc
            self._ready = True
            threading.Thread(target=self._read, args=(self._queue,), daemon=True, name="process-tree").start()
            return True

    def _register(self, pid: int, tree: Tree) -> bool:
        """Ask to be told when ``pid`` forks or exits; False when it has already ended."""
        with self._lock:
            self._owners[pid] = tree
        event = select.kevent(pid, select.KQ_FILTER_PROC, select.KQ_EV_ADD | select.KQ_EV_CLEAR, WATCHED)
        try:
            self._queue.control([event], 0, 0)
        except OSError:
            with self._lock:
                if self._owners.get(pid) is tree:
                    del self._owners[pid]
            tree.ended(pid)
            return False
        return True

    def _reaped_children(self, pid: int) -> bool:
        """Whether ``pid`` has waited for a child already, or that cannot be told."""
        words = (ctypes.c_uint64 * RUSAGE_WORDS)()
        if self._libproc.proc_pid_rusage(pid, RUSAGE_V2, words) != 0:
            return True
        return any(words[word] for word in CHILD_WORDS)

    def _children(self, pid: int) -> list[int] | None:
        """The processes whose parent is ``pid``; None when the list was cut short."""
        found = (ctypes.c_int * LISTED)()
        size = self._libproc.proc_listpids(PPID_ONLY, pid, found, ctypes.sizeof(found))
        if size >= ctypes.sizeof(found):
            return None
        return [found[i] for i in range(max(size, 0) // ctypes.sizeof(ctypes.c_int))]

    def _following(self, pid: int, tree: Tree) -> bool:
        with self._lock:
            return self._owners.get(pid) is tree

    def _scan(self, parent: int, tree: Tree) -> bool:
        """Follow ``parent``'s children and theirs; False when a list was cut short."""
        work, whole = [parent], True
        while work:
            listed = self._children(work.pop())
            if listed is None:
                whole = False
                continue
            if listed:
                tree.forked()
            for child in listed:
                if not self._following(child, tree) and tree.add(child) and self._register(child, tree):
                    work.append(child)
        return whole

    def _handle(self, event: select.kevent) -> None:
        with self._lock:
            tree = self._owners.get(event.ident)
            if event.fflags & select.KQ_NOTE_EXIT:
                self._owners.pop(event.ident, None)
        if tree is None:
            return
        if event.fflags & select.KQ_NOTE_EXIT:
            tree.ended(event.ident)
        if event.fflags & select.KQ_NOTE_FORK:
            tree.forked()
            if not self._scan(event.ident, tree):
                tree.complete = False

    def _read(self, queue: select.kqueue) -> None:
        while queue is self._queue and self._ready:
            try:
                select.select([queue], [], [])
            except (OSError, ValueError):
                if queue is self._queue:
                    logger.exception("the processes of sandboxed runs are no longer followed")
                    self._stop()
                return
            self.flush()

    def _stop(self) -> None:
        """Mark every tree still followed as not complete and start afresh at the next `follow`."""
        with self._lock:
            for tree in self._owners.values():
                tree.complete = False
            self._owners.clear()
            self._ready = False
            queue, self._queue = self._queue, None
        if queue is not None:
            with contextlib.suppress(OSError, ValueError):
                queue.close()


_FOLLOWER = _Follower()


def follow(root: int) -> Tree:
    """The `Tree` of the process ``root`` and of every process it, or they, start from now on."""
    return _FOLLOWER.follow(root)


def release(tree: Tree) -> None:
    """Stop following the processes of ``tree``."""
    _FOLLOWER.release(tree)


def flush() -> None:
    """Take in every fork and exit the kernel has reported so far."""
    _FOLLOWER.flush()

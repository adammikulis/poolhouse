"""The processes one confined run has started, followed from the moment the run began.

macOS tells a process that it forked (``kqueue``, ``NOTE_FORK``) without saying which child it made, so
each notification is answered by listing the children of that process (``proc_listpids`` of libproc)
and following them in turn. Where there is no kqueue or libproc, and for a process that had already
ended when it was first looked at, the tree is not complete.
"""

from __future__ import annotations

import ctypes
import logging
import select
import threading
from collections.abc import Iterable

__all__ = ["Tree", "follow"]

logger = logging.getLogger("poolhouse.sandbox")

PPID_ONLY = 6
"""The ``proc_listpids`` type that lists the processes whose parent is the id given."""
LISTED = 4096
"""Children listed at once; a process with more makes its tree not complete."""
KEPT = 65536
"""Processes one tree keeps; a run that starts more is not followed past them and its tree is not complete."""
WATCHED = (getattr(select, "KQ_NOTE_FORK", 0) | getattr(select, "KQ_NOTE_EXIT", 0))


class Tree:
    """The pids of one run's processes: the process it started and every descendant seen starting.
    ``complete`` is False when a process could not be followed from its start."""

    def __init__(self, root: int) -> None:
        self.root = root
        self.complete = True
        self._lock = threading.Lock()
        self._pids = {root}

    def __contains__(self, pid: object) -> bool:
        with self._lock:
            return pid in self._pids

    def holds_any(self, pids: Iterable[int]) -> bool:
        """Whether any of ``pids`` is in the tree."""
        with self._lock:
            return not self._pids.isdisjoint(pids)

    def add(self, pid: int) -> bool:
        """Put ``pid`` in the tree; False when it already was or the tree is full."""
        with self._lock:
            if pid in self._pids:
                return False
            if len(self._pids) >= KEPT:
                self.complete = False
                return False
            self._pids.add(pid)
            return True


class _Follower:
    """One kqueue, and a thread that reads it, for every tree followed in this process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
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
        if not self._register(root, tree) or not self._scan(root, tree):
            tree.complete = False
        return tree

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
                self._queue = select.kqueue()
            except (OSError, AttributeError) as exc:
                logger.warning("the processes of a sandboxed run cannot be followed: %s", exc)
                return False
            self._libproc = libproc
            threading.Thread(target=self._read, daemon=True, name="process-tree").start()
            self._ready = True
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
                self._owners.pop(pid, None)
            return False
        return True

    def _children(self, pid: int) -> list[int] | None:
        """The processes whose parent is ``pid``; None when the list was cut short."""
        found = (ctypes.c_int * LISTED)()
        size = self._libproc.proc_listpids(PPID_ONLY, pid, found, ctypes.sizeof(found))
        if size >= ctypes.sizeof(found):
            return None
        return [found[i] for i in range(max(size, 0) // ctypes.sizeof(ctypes.c_int))]

    def _scan(self, parent: int, tree: Tree) -> bool:
        """Follow ``parent``'s children and theirs; False when a list was cut short."""
        work, whole = [parent], True
        while work:
            listed = self._children(work.pop())
            if listed is None:
                whole = False
                continue
            for child in listed:
                if tree.add(child) and self._register(child, tree):
                    work.append(child)
        return whole

    def _read(self) -> None:
        try:
            while True:
                for event in self._queue.control(None, 64, None):
                    with self._lock:
                        tree = self._owners.get(event.ident)
                        if event.fflags & select.KQ_NOTE_EXIT:
                            self._owners.pop(event.ident, None)
                    if tree is not None and event.fflags & select.KQ_NOTE_FORK and not self._scan(event.ident, tree):
                        tree.complete = False
        except (OSError, ctypes.ArgumentError):
            logger.exception("the processes of sandboxed runs are no longer followed")
            self._stop()

    def _stop(self) -> None:
        """Mark every tree still followed as not complete and start afresh at the next `follow`."""
        with self._lock:
            for tree in self._owners.values():
                tree.complete = False
            self._owners.clear()
            self._ready = False


_FOLLOWER = _Follower()


def follow(root: int) -> Tree:
    """The `Tree` of the process ``root`` and of every process it, or they, start from now on."""
    return _FOLLOWER.follow(root)

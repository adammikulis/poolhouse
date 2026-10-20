"""Process-bound reader/writer admission for app-owned managed interpreter code.

Intent excludes new readers while existing work drains. File contents grant no authority:
only live OS locks matter, and process exit releases them without stale-marker recovery.
"""
from __future__ import annotations

import os
import stat
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps

from poolhouse import home, lock, maintenance_windows, private_path


@dataclass
class _State:
    mutex: threading.RLock = field(default_factory=threading.RLock)
    readers: dict[int, int] = field(default_factory=dict)
    descriptor: int | None = None
    writer: int | None = None
    retained: int = 0


_states: dict[tuple[int, str], _State] = {}
_states_lock = threading.Lock()


def _place():
    folder = home.state('managed-code')
    if any(path.is_symlink() for path in (folder, *folder.parents)):
        raise PermissionError('Managed code admission cannot use linked directories')
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if private_path.problem(folder):
        raise PermissionError('Managed code admission requires a private owned directory')
    with _states_lock:
        state = _states.setdefault((os.getpid(), str(folder)), _State())
    return folder, state


def _same(fd, path):
    actual, named = os.fstat(fd), path.lstat()
    if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
            or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
            or private_path.problem(path)):
        raise PermissionError('Managed code admission requires unchanged private regular locks')


def _open(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    checked = False
    try:
        _same(fd, path)
        checked = True
        return fd
    finally:
        if not checked:
            os.close(fd)


def _take(fd, *, shared):
    if sys.platform == 'win32':
        return maintenance_windows.take(fd, shared=shared)
    return lock.take(fd, shared=shared)


def _release(fd):
    if sys.platform == 'win32':
        maintenance_windows.release(fd)
    else:
        lock.release(fd)


def _enter(folder, state, thread, existing):
    intent_fd = None
    try:
        if not existing:
            intent_fd = _open(folder / 'intent.lock')
            if not _take(intent_fd, shared=True):
                os.close(intent_fd)
                intent_fd = None
                return False
        with state.mutex:
            if state.descriptor is None:
                fd = _open(folder / 'content.lock')
                if not _take(fd, shared=True):
                    os.close(fd)
                else:
                    state.descriptor = fd
            if state.descriptor is not None:
                checked = False
                try:
                    _same(state.descriptor, folder / 'content.lock')
                    checked = True
                    state.readers[thread] = 1
                    return True
                finally:
                    if not checked and not state.readers and not state.retained:
                        _release(state.descriptor)
                        os.close(state.descriptor)
                        state.descriptor = None
    finally:
        if intent_fd is not None:
            _release(intent_fd)
            os.close(intent_fd)
    return False


@contextmanager
def read(*, existing: bool = False):
    """Hold managed code stable; only fixed accounting calls may bypass writer intent."""
    folder, state = _place()
    thread = threading.get_ident()
    with state.mutex:
        owned_writer = state.writer == thread
        if thread in state.readers:
            state.readers[thread] += 1
            nested = True
        else:
            nested = False
    if owned_writer:
        yield
        return
    while not nested:
        nested = _enter(folder, state, thread, existing)
        if not nested:
            time.sleep(0.02)
    try:
        yield
    finally:
        with state.mutex:
            state.readers[thread] -= 1
            if not state.readers[thread]:
                del state.readers[thread]
            if not state.readers and not state.retained:
                _release(state.descriptor)
                os.close(state.descriptor)
                state.descriptor = None


def reading(function):
    """Protect a complete app-managed operation, including its child lifetime."""
    @wraps(function)
    def guarded(*args, **kwargs):
        with read():
            return function(*args, **kwargs)
    return guarded


def spawn(starter, *args, **kwargs):
    """Protect an owned asynchronous child without treating its caller as forever nested."""
    _, state = _place()
    with read():
        process = starter(*args, **kwargs)
        with state.mutex:
            state.retained += 1
        def finished():
            try:
                process.wait()
            except OSError:
                return  # An uncertain child keeps its admission lease; never assume it ended.
            with state.mutex:
                state.retained -= 1
                if not state.readers and not state.retained:
                    _release(state.descriptor)
                    os.close(state.descriptor)
                    state.descriptor = None
        threading.Thread(target=finished, daemon=True, name='managed-process-admission').start()
        return process


class Writer:
    def __init__(self, folder, state):
        self.folder, self.state = folder, state

    @contextmanager
    def exclusive(self):
        """Take an idle write turn without waiting or holding device compute."""
        fd = _open(self.folder / 'content.lock')
        taken = False
        try:
            taken = _take(fd, shared=False)
            if not taken:
                raise lock.Busy('Existing managed work is still using its runtime')
            with self.state.mutex:
                if self.state.readers or self.state.retained or self.state.writer is not None:
                    raise lock.Busy('Existing managed work is still using its runtime')
                self.state.writer = threading.get_ident()
            try:
                yield
            finally:
                with self.state.mutex:
                    self.state.writer = None
        finally:
            if taken:
                _release(fd)
            os.close(fd)


@contextmanager
def intent():
    """Close admission while existing readers finish; a crashed owner cannot retain it."""
    folder, state = _place()
    fd = _open(folder / 'intent.lock')
    taken = False
    try:
        taken = _take(fd, shared=False)
        if not taken:
            raise lock.Busy('Another managed runtime update is already pending')
        yield Writer(folder, state)
    finally:
        if taken:
            _release(fd)
        os.close(fd)

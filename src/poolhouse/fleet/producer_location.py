"""Private discovery metadata ownership; this creates no producer authority."""
from __future__ import annotations

import json
import os
import secrets
import stat
import threading
from contextlib import suppress
from pathlib import Path

from poolhouse import home, lock, platform, real_machine

from .producer_bootstrap import _signature, read_existing
from .studio_epoch import directory

LOCATOR = 'producer-control.json'
LOCK = LOCATOR + '.lock'


def _identity(info):
    return info.st_dev, info.st_ino


def _private(info, *, links=1):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != links):
        raise PermissionError('Producer metadata must be an account-private regular file')


def _remove(fd, name, expected):
    try:
        if _identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) == expected:
            os.unlink(name, dir_fd=fd)
    except FileNotFoundError:
        pass


class Location:
    """Retain exclusive software ownership until all owned metadata is removed."""

    def __init__(self, root):
        self.root = Path(root)
        if not self.root.is_absolute() or '..' in self.root.parts:
            raise ValueError('An absolute installed directory is required')
        if self.root.is_relative_to(home.account_roots()[0]):
            real_machine.require('Producer discovery metadata')
        self._guard = threading.RLock()
        self._closed, self._context = False, None
        self._fd, self._lock = None, None
        self._locator, self._path = None, None
        self._parts = {}
        initialized = False
        try:
            self._context = directory(self.root)
            self._fd = self._context.__enter__()
            self._root = _identity(os.fstat(self._fd))
            if not platform.private_dir('/dev/fd/' + str(self._fd)):
                raise PermissionError('Producer directory could not be made private')
            self._directory()
            read_existing(self.root)
            self._lock = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=self._fd)
            _private(os.fstat(self._lock))
            self._lock_identity = _identity(os.fstat(self._lock))
            self._locked = lock.take(self._lock)
            if not self._locked:
                raise PermissionError('The producer discovery location is already in use')
            self.check()
            self._stale()
            initialized = True
        finally:
            if not initialized:
                self.close()

    def _directory(self):
        info = os.fstat(self._fd)
        if (_identity(info) != self._root or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise PermissionError('Producer directory changed')
        with directory(self.root) as current:
            if _identity(os.fstat(current)) != self._root:
                raise PermissionError('Producer directory changed')

    def check(self):
        with self._guard:
            if self._closed:
                raise PermissionError('Producer discovery location is closed')
            self._directory()
            for info in (os.fstat(self._lock), os.stat(LOCK, dir_fd=self._fd, follow_symlinks=False)):
                _private(info)
                if _identity(info) != self._lock_identity:
                    raise PermissionError('Producer discovery ownership changed')
            return True

    def _stale(self):
        try:
            before = os.stat(LOCATOR, dir_fd=self._fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if before.st_nlink not in (1, 2):
            raise PermissionError('Producer discovery metadata is unsafe')
        _private(before, links=before.st_nlink)
        self.check()
        _remove(self._fd, LOCATOR, _identity(before))
        if self._exists(LOCATOR):
            raise PermissionError('Producer discovery metadata changed')
        os.fsync(self._fd)

    def _exists(self, name):
        try:
            os.stat(name, dir_fd=self._fd, follow_symlinks=False)
            return True
        except FileNotFoundError:
            return False

    def _socket(self, value):
        path = Path(value)
        if (not isinstance(value, (str, Path)) or not path.is_absolute() or '..' in path.parts
                or '\0' in str(path) or len(os.fsencode(path)) > 103 or not path.is_relative_to(self.root)):
            raise ValueError('An owned private bootstrap socket is required')
        parent = os.dup(self._fd)
        try:
            for name in path.relative_to(self.root).parts[:-1]:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
                info = os.fstat(parent)
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise PermissionError('Bootstrap socket directory is not private')
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise PermissionError('An owned private bootstrap socket is required')
        finally:
            os.close(parent)
        return str(path), _identity(info)

    def publish(self, bootstrap_path):
        with self._guard:
            self.check()
            value, endpoint = self._socket(bootstrap_path)
            if self._locator is not None:
                info = os.stat(LOCATOR, dir_fd=self._fd, follow_symlinks=False)
                _private(info)
                if (value != self._path or endpoint != self._endpoint
                        or _signature(info) != self._published):
                    raise PermissionError('Producer discovery metadata changed')
                return self.root / LOCATOR
            name = LOCATOR + '.' + secrets.token_hex(16) + '.part'
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self._fd)
            original = _identity(os.fstat(handle))
            self._parts[name] = original
            published = False
            try:
                with os.fdopen(handle, 'wb') as output:
                    output.write(json.dumps({'version': 1, 'bootstrap_path': value}).encode())
                    output.flush()
                    os.fsync(output.fileno())
                self.check()
                if _identity(os.stat(name, dir_fd=self._fd, follow_symlinks=False)) != original:
                    raise PermissionError('Producer discovery metadata changed')
                if self._socket(value)[1] != endpoint:
                    raise PermissionError('Bootstrap socket changed')
                os.link(name, LOCATOR, src_dir_fd=self._fd, dst_dir_fd=self._fd, follow_symlinks=False)
                self._locator = original
                if _identity(os.stat(LOCATOR, dir_fd=self._fd, follow_symlinks=False)) != original:
                    raise PermissionError('Producer discovery metadata changed')
                _remove(self._fd, name, original)
                self._parts.pop(name)
                info = os.stat(LOCATOR, dir_fd=self._fd, follow_symlinks=False)
                _private(info)
                self.check()
                os.fsync(self._fd)
                if self._socket(value)[1] != endpoint:
                    raise PermissionError('Bootstrap socket changed')
                self._endpoint = endpoint
                self._published = _signature(info)
                self._path = value
                published = True
                return self.root / LOCATOR
            finally:
                if not published:
                    _remove(self._fd, name, original)
                    self._parts.pop(name, None)
                    if self._locator == original:
                        _remove(self._fd, LOCATOR, original)
                        self._locator = None

    def close(self):
        with self._guard:
            if self._closed:
                return
            self._closed = True
            fd, context, handle = self._fd, self._context, self._lock
            self._fd = self._context = self._lock = None
            try:
                if fd is not None:
                    for name, original in self._parts.items():
                        _remove(fd, name, original)
                    if self._locator is not None:
                        _remove(fd, LOCATOR, self._locator)
                    os.fsync(fd)
            finally:
                if handle is not None:
                    os.close(handle)
                if context is not None and fd is not None:
                    with suppress(OSError):
                        context.__exit__(None, None, None)

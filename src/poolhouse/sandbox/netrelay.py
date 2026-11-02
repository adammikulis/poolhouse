"""The parent's end of the relay for `Net.only` on Linux: one Unix socket per named port, each carried to that port of the host's loopback.

The directory holds nothing but those sockets, is made by the parent for one run (owner only, under a name nobody can guess) and is
bound read-only into the confined process's namespace, which cannot add, replace or remove a socket in it. A socket's relay connects to
``127.0.0.1`` and the one port the socket was made for; a connection to the socket is carried to that port and to nothing else.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import socket
import tempfile
import threading
from pathlib import Path

import psutil

from poolhouse.sandbox import netforward

__all__ = ["Relay", "sweep"]

NAME = re.compile(r"poolhouse-net-(\d+)-(\d+)-\w+")
"""A relay directory's name holds the pid of the process that made it and that process's start time (whole seconds), so a directory
left by a process that was killed can be told from one a live process still uses."""


SOCKET_PATH_MOST = 103
"""The longest path a Unix socket can be bound to on either system (`sun_path` is 104 bytes on macOS and 108 on Linux, each with its end)."""


def _base(name: str) -> str:
    """Where a relay directory is made: the temporary directory, unless a socket in it would have too long a path (macOS temporary
    directories are long), then `/tmp`."""
    base = os.path.realpath(tempfile.gettempdir())
    longest = len(base) + 1 + len(name) + len("/p65535.sock")
    return base if longest <= SOCKET_PATH_MOST else "/tmp"  # noqa: S108 - a short fallback, the directory itself is private


def _alive(pid: int, started: int) -> bool:
    try:
        return int(psutil.Process(pid).create_time()) == started
    except psutil.Error:
        return False


def sweep() -> None:
    """Remove the relay directories of processes that no longer exist (a hard kill leaves them). Only this user's plain directories whose
    name is exactly a relay directory's are touched."""
    roots = {Path(tempfile.gettempdir()), Path("/tmp")}  # noqa: S108 - where `_base` falls back to
    for entry in (candidate for root in roots for candidate in root.glob("poolhouse-net-*")):
        found = NAME.fullmatch(entry.name)
        try:
            info = entry.lstat()
        except OSError:
            continue
        if found and entry.is_dir() and not entry.is_symlink() and info.st_uid == os.getuid() and not _alive(int(found[1]), int(found[2])):
            shutil.rmtree(entry, ignore_errors=True)


def _toward(port: int):
    def connect() -> socket.socket:
        made = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            made.connect(("127.0.0.1", port))
        except OSError:
            made.close()
            raise
        return made
    return connect


class Relay:
    """Listening Unix sockets, one per port, until `close`."""

    def __init__(self, ports: tuple[int, ...]) -> None:
        sweep()
        started = int(psutil.Process().create_time())
        prefix = f"poolhouse-net-{os.getpid()}-{started}-"
        self.directory = os.path.realpath(tempfile.mkdtemp(prefix=prefix, dir=_base(prefix + "xxxxxxxx")))
        self._stopping = threading.Event()
        self._listeners: list[socket.socket] = []
        self._threads: list[threading.Thread] = []
        try:
            for port in ports:
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._listeners.append(listener)
                listener.bind(str(Path(self.directory) / f"p{port}.sock"))
                listener.listen(netforward.BACKLOG)
                thread = threading.Thread(target=netforward.accept, args=(listener, _toward(port), self._stopping), daemon=True)
                self._threads.append(thread)
                thread.start()
        except OSError:
            self.close()
            raise

    def close(self) -> None:
        """Stop carrying connections and remove the sockets."""
        self._stopping.set()
        for listener in self._listeners:
            with contextlib.suppress(OSError):
                listener.close()
        for thread in self._threads:
            thread.join(2)
        shutil.rmtree(self.directory, ignore_errors=True)

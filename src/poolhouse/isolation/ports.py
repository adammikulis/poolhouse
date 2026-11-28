"""A launch's window of loopback ports, and the one place its box gets a socket to listen on.

The window is a block of ports below the system's ephemeral range, none of them in use when it is chosen and no two live launches sharing one
(a file lock per block). The profile lets the box reach those ports over TCP and serve on a socket it is handed; it binds none. This bridge, in
the launcher, binds ``127.0.0.1`` or ``::1`` on a port of the window and passes the socket over a Unix socket (`loopback_window`), so nothing the
box serves is on any other address. It answers the processes the launch admits (`serving.admitted`) and closes every other connection unread.
macOS only: a Linux box has a loopback of its own.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import secrets
import socket
import subprocess
import threading
from collections.abc import Callable, Collection
from contextlib import AbstractContextManager
from pathlib import Path

from poolhouse import lock
from poolhouse.isolation.serving import admitted

__all__ = ["FIRST", "LAST", "SIZE", "PortBridge", "in_use"]

FIRST, LAST, SIZE = 41000, 48999, 128
"""The ports a window is cut from, below macOS's ephemeral range (49152 on) and clear of every fixed port of Poolhouse and of 8080 to 8099, and
the ports in one window."""
NETSTAT = "/usr/sbin/netstat"
ASKED_BYTES = 256


def in_use() -> frozenset[int]:
    """Every TCP port some socket of this machine holds, on any address and of any user, as the system lists them. `OSError` when it cannot be
    asked, so no window is opened over a port nobody looked at."""
    try:
        said = subprocess.run([NETSTAT, "-an", "-p", "tcp"], capture_output=True, text=True, timeout=10, check=True, stdin=subprocess.DEVNULL).stdout
    except subprocess.SubprocessError as why:
        raise OSError(f"the ports in use could not be listed: {why}") from why
    found = (line.split() for line in said.splitlines() if line.startswith("tcp"))
    return frozenset(int(port) for fields in found if len(fields) > 3 and (port := fields[3].rpartition(".")[2]).isdigit())


class PortBridge:
    """The window of one launch: `start` chooses it and serves it, `stop` gives it up. ``root`` is a short private directory for the socket,
    ``locks`` the directory every launch of this account claims its block in, ``avoid`` ports no window may hold (a model server's, the
    harness's own). ``admit`` is told the process id of each connecting process and says whether it is one of the launch's: no window answers
    every process of the user."""

    def __init__(self, root: str, locks: Path, avoid: Collection[int] = (), *, admit: Callable[[int], bool]) -> None:
        self.path = os.path.realpath(root) + "/ports"
        self.locks, self.avoid, self._admit = locks, frozenset(avoid), admit
        self.ports = range(0)
        self._claim: AbstractContextManager[Path] | None = None
        self._next = 0
        self._server: socket.socket | None = None
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def setting(self) -> str:
        """The value of `loopback_window.ENV` in the box."""
        return f"{self.ports.start}-{self.ports.stop - 1}@{self.path}"

    def _choose(self) -> range:
        busy = in_use() | self.avoid
        blocks = list(range(FIRST, LAST - SIZE + 2, SIZE))
        offset = secrets.randbelow(len(blocks))
        for first in (*blocks[offset:], *blocks[:offset]):
            block = range(first, first + SIZE)
            if busy.intersection(block):
                continue
            claim = lock.only_one(self.locks / f"{first}.lock", wait=False)
            try:
                claim.__enter__()   # held until `stop`
            except lock.Busy:
                continue
            self._claim = claim
            return block
        raise OSError(errno.EADDRINUSE, "every window of loopback ports is in use")

    def start(self) -> None:
        self.ports = self._choose()
        server = socket.socket(socket.AF_UNIX)
        server.bind(self.path)
        server.listen(64)
        server.settimeout(0.2)
        self._server = server
        self._thread = threading.Thread(target=self._serve, args=(server,), daemon=True)
        self._thread.start()

    def _serve(self, server: socket.socket) -> None:
        while not self._stopped.is_set():
            try:
                connection, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection, contextlib.suppress(OSError):
                connection.settimeout(2.0)
                if admitted(connection, self._admit):
                    self._answer(connection)

    def _answer(self, connection: socket.socket) -> None:
        try:
            asked = json.loads(connection.recv(ASKED_BYTES))
            family = socket.AF_INET6 if asked["family"] == 6 else socket.AF_INET
            wanted, reuse = int(asked["port"]), bool(asked["reuse"])
            held = self._bound(family, wanted, reuse)
        except (ValueError, KeyError, TypeError, OSError) as why:
            number = why.errno if isinstance(why, OSError) and why.errno else errno.EPERM
            connection.sendall(json.dumps({"errno": number}).encode())
            return
        with held:
            socket.send_fds(connection, [json.dumps({"port": held.getsockname()[1]}).encode()], [held.fileno()])
            connection.recv(1)   # the box says it has it: macOS can break a socket still in flight when its sender closes it

    def _bound(self, family: int, wanted: int, reuse: bool) -> socket.socket:
        """A socket bound to loopback on ``wanted``, or on the next free port of the window for 0; `OSError` for a port outside the window."""
        if wanted and wanted not in self.ports:
            raise OSError(errno.EPERM, "that port is not in this window")
        for _ in range(1 if wanted else len(self.ports)):
            port = wanted
            if not port:
                port, self._next = self.ports[self._next % len(self.ports)], self._next + 1
            made = socket.socket(family, socket.SOCK_STREAM)
            try:
                if reuse or not wanted:
                    made.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                made.bind(("::1" if family == socket.AF_INET6 else "127.0.0.1", port))
                return made
            except OSError:
                made.close()
                if wanted:
                    raise
        raise OSError(errno.EADDRINUSE, "every port of the window is taken")

    def stop(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(5)
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(OSError):
                Path(self.path).unlink()
        if self._claim is not None:
            self._claim.__exit__(None, None, None)
            self._claim = None

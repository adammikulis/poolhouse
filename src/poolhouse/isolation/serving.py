"""One listening socket and the threads that serve it: the shared body of the model bridge, the egress proxy and the session bridge.

Each connection is one thread that runs the service's handler once and is then closed; no connection is kept for a second request. The
number of live connections is bounded by the machine's own limits (`connection_bound`), a connection past it is closed at once and never
queued, and `stop` closes every live connection. Nothing here waits for anyone but the other end, and only as long as `IDLE_SECONDS`.
"""

from __future__ import annotations

import contextlib
import errno
import os
import socket
import struct
import threading
from collections.abc import Callable
from pathlib import Path

from poolhouse.sandbox.run import fair_share

__all__ = ["IDLE_SECONDS", "Server", "admitted", "connection_bound", "credentials", "tell"]

IDLE_SECONDS = 180.0
"""The longest the other end may be silent before a step is abandoned: the project's one request timeout (`poolhouse.http.Options.timeout`), an
existing constant with no recorded reason, unchanged. A service takes it as an argument, so a test can ask for a shorter one."""
SOL_LOCAL = 0
LOCAL_PEERCRED = 0x001
LOCAL_PEERPID = 0x002
"""The macOS options of `getsockopt` that name the other end of a unix socket (`<sys/un.h>`); Linux has `SO_PEERCRED`."""
RETRY = (errno.EMFILE, errno.ENFILE, errno.ECONNABORTED, errno.ENOBUFS, errno.ENOMEM, errno.EINTR)
"""What `accept` fails with while the service is sound: out of descriptors or memory for now, or a connection that reset before it was taken."""
PAUSE_SECONDS = 0.05
XUCRED_UID_AT = 4
"""The offset of `cr_uid` in macOS's `struct xucred` (a four-byte version comes first)."""


def tell(events: Callable[[str, dict[str, object]], None] | None, surface: str, event: str, **fields: object) -> None:
    """Report ``event`` of ``surface`` to the sentinel's callback, if there is one; a callback that fails changes nothing for the session."""
    if events is not None:
        try:
            events(event, {"surface": surface, **fields})
        except (OSError, ValueError, RuntimeError):
            return


def connection_bound() -> int:
    """How many connections a service holds at once: the system's per-user process room divided among the live confinements
    (`sandbox.fair_share`), and never more than the operating system's own listen queue (`socket.SOMAXCONN`)."""
    room = fair_share()
    return max(1, min(room, socket.SOMAXCONN) if room else socket.SOMAXCONN)


def credentials(sock: socket.socket) -> tuple[int, int]:
    """``(pid, uid)`` of the process at the other end of a connected unix socket, as the operating system reports them; ``pid`` is -1
    where the platform names none, and ``OSError`` where it names nothing at all."""
    if hasattr(socket, "SO_PEERCRED"):
        pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        return pid, uid
    credential = sock.getsockopt(SOL_LOCAL, LOCAL_PEERCRED, 76)
    uid = struct.unpack_from("I", credential, XUCRED_UID_AT)[0]
    try:
        return int(sock.getsockopt(SOL_LOCAL, LOCAL_PEERPID)), uid
    except OSError:
        return -1, uid


def admitted(connection: socket.socket, admit: Callable[[int], bool]) -> bool:
    """Whether the process at the other end of a connected unix socket runs as this user and ``admit`` admits its pid: the one check of who a
    launch's unix sockets answer. A connection the system names no process of is not admitted."""
    pid, uid = credentials(connection)
    return uid == os.getuid() and pid >= 0 and bool(admit(pid))


class Server:
    """A listener on ``address`` whose connections are each handed once to ``handler(connection)`` on a thread of its own."""

    def __init__(self, family: int, address: str | tuple[str, int], handler: Callable[[socket.socket], None], *, limit: int | None = None,  # noqa: PLR0913
                 admit: Callable[[socket.socket], bool] | None = None, stopped: Callable[[], None] | None = None) -> None:
        self.family, self.address, self.handler, self.stopped = family, address, handler, stopped
        self.limit = limit or connection_bound()
        self.admit = admit
        self._listener: socket.socket | None = None
        self._live: set[socket.socket] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pause = threading.Event()
        self.refused = 0
        """How many connections were closed at once: past the bound, or not admitted."""

    def start(self) -> tuple[str, int] | str:
        """Bind and listen, and serve on a thread; the address it is bound to (an ephemeral port is named only now)."""
        listener = socket.socket(self.family, socket.SOCK_STREAM)
        try:
            if self.family == socket.AF_INET:
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(self.address)
            listener.listen(self.limit)
        except OSError:
            listener.close()
            raise
        self._listener = listener
        self._thread = threading.Thread(target=self._accept, args=(listener,), daemon=True)
        self._thread.start()
        return listener.getsockname()

    def _accept(self, listener: socket.socket) -> None:
        while True:
            try:
                connection, _ = listener.accept()
            except OSError as why:
                if why.errno in RETRY and self._listener is listener:
                    self._pause.wait(PAUSE_SECONDS)   # no descriptor for it now, or it reset before it was taken: serving goes on
                    continue
                if self._listener is listener and self.stopped is not None:   # the listener failed; it was not stopped
                    with contextlib.suppress(OSError, ValueError, RuntimeError):
                        self.stopped()
                return
            with self._lock:
                full = len(self._live) >= self.limit
                if not full:
                    self._live.add(connection)
            if full or (self.admit is not None and not self._admitted(connection)):
                self.refused += 1
                self._drop(connection)
                continue
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _admitted(self, connection: socket.socket) -> bool:
        try:
            return bool(self.admit(connection)) if self.admit is not None else True
        except (OSError, ValueError, struct.error):
            return False

    def _serve(self, connection: socket.socket) -> None:
        try:
            with contextlib.suppress(OSError, ValueError):
                self.handler(connection)
        finally:
            self._drop(connection)

    def _drop(self, connection: socket.socket) -> None:
        with self._lock:
            self._live.discard(connection)
        with contextlib.suppress(OSError):
            connection.shutdown(socket.SHUT_WR)
            connection.setblocking(False)
            while connection.recv(65536):   # what the other end sent unread is taken, so closing does not reset the answer already written
                pass
        connection.close()

    def live(self) -> int:
        """How many connections are open now."""
        with self._lock:
            return len(self._live)

    def stop(self) -> None:
        """Stop listening and close every live connection."""
        self._pause.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            with contextlib.suppress(OSError):
                listener.shutdown(socket.SHUT_RDWR)
            listener.close()
        with self._lock:
            held = list(self._live)
        for connection in held:
            self._drop(connection)
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self.family == socket.AF_UNIX and isinstance(self.address, str):
            with contextlib.suppress(OSError):
                Path(self.address).unlink()

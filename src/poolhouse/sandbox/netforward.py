"""The two ends of a relay that gives a confined process a few ports of the host's loopback and no other network (Linux).

A confined process on Linux runs in a network namespace of its own, where only its own loopback exists. A port of the host's loopback
is reached through a Unix socket in a directory the parent made and bound into the namespace: the parent listens on the socket and
connects to ``127.0.0.1`` and the one port the socket was made for (`poolhouse.sandbox.netrelay`), and `forward_then_run` below, started inside
the namespace before the command, listens on the same port of the namespace's loopback and connects to the socket. Neither end names a
destination in the bytes it copies, so a command cannot ask for another port.

This file is run by the interpreter inside the confinement (``python -I -S netforward.py``), so it imports the standard library only.
"""

from __future__ import annotations

import os
import selectors
import socket
import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

__all__ = ["BACKLOG", "CONNECTIONS", "accept", "carried_to", "forward_then_run", "pump"]

CONNECTIONS = 32
"""Connections a port carries at once; one over it is closed. Each takes a thread of the process that serves it, and a thread in the
namespace counts toward the process bound of the run."""
BACKLOG = 32
CHUNK = 65536


def pump(left: socket.socket, right: socket.socket, stopping: threading.Event) -> None:
    """Copy bytes both ways between two connected sockets until either side closes or ``stopping`` is set, then close both."""
    with left, right, selectors.DefaultSelector() as ready:
        left.settimeout(1)
        right.settimeout(1)
        ready.register(left, selectors.EVENT_READ, right)
        ready.register(right, selectors.EVENT_READ, left)
        try:
            while ready.get_map() and not stopping.is_set():
                for key, _ in ready.select(0.25):
                    data = key.fileobj.recv(CHUNK)
                    if data:
                        key.data.sendall(data)
                    else:
                        ready.unregister(key.fileobj)
                        key.data.shutdown(socket.SHUT_WR)
        except OSError:
            return


def accept(listener: socket.socket, connect: Callable[[], socket.socket], stopping: threading.Event) -> None:
    """Take each connection to ``listener`` and pump it to a new socket ``connect`` makes, up to `CONNECTIONS` at once, until
    ``stopping`` is set. A connection that cannot be carried is closed."""
    room = threading.BoundedSemaphore(CONNECTIONS)
    listener.settimeout(0.25)

    def carry(near: socket.socket) -> None:
        try:
            try:
                far = connect()
            except OSError:
                near.close()
                return
            pump(near, far, stopping)
        finally:
            room.release()

    while not stopping.is_set():
        try:
            near, _ = listener.accept()
        except TimeoutError:
            continue
        except OSError:
            return
        if not room.acquire(blocking=False):
            near.close()
            continue
        threading.Thread(target=carry, args=(near,), daemon=True).start()


def carried_to(path: str) -> Callable[[], socket.socket]:
    def connect() -> socket.socket:
        made = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            made.connect(path)
        except OSError:
            made.close()
            raise
        return made
    return connect


def forward_then_run(argv: Sequence[str] | None = None) -> int:
    """``netforward.py DIRECTORY PORT,PORT -- COMMAND ...``: listen on those ports of this namespace's loopback, carry each connection
    to ``DIRECTORY/p<port>.sock`` from a process of its own, and become the command. The carrier ends with the namespace."""
    directory, ports, _, *command = list(sys.argv[1:] if argv is None else argv)
    listeners = []
    for port in ports.split(","):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", int(port)))
        listener.listen(BACKLOG)
        listeners.append((listener, carried_to(str(Path(directory) / f"p{port}.sock"))))
    if os.fork() == 0:
        quiet = os.open(os.devnull, os.O_RDWR)
        for descriptor in range(3):
            os.dup2(quiet, descriptor)
        stopping = threading.Event()
        for listener, connect in listeners:
            threading.Thread(target=accept, args=(listener, connect, stopping), daemon=True).start()
        stopping.wait()
        os._exit(0)
    for listener, _connect in listeners:
        listener.close()
    try:
        os.execvp(command[0], command)  # noqa: S606 - the command is the launcher's argv
    except OSError as error:
        sys.stderr.write(f"netforward: cannot start {command[0]}: {error}\n")
        return 127


if __name__ == "__main__":
    raise SystemExit(forward_then_run())

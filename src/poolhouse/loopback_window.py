"""The box's side of its window of loopback ports: a confined process binds no port itself, so the launcher binds one and hands it over.

A Seatbelt rule that names a loopback port also matches the wildcard and the machine's network address, so a box that may bind could listen to
the whole network. A confined launch therefore refuses every bind of its window and answers on a Unix socket of its own (`isolation.ports`):
it binds ``127.0.0.1`` or ``::1`` and passes the bound socket back. `install` makes `socket.bind` ask there for a loopback TCP port 0 or a
port of the window, so a program that serves on loopback runs unchanged; every other bind is the system's own, which the box refuses.

Standard library only: the launcher writes this file into the box as ``sitecustomize.py``, which every Python of the box imports at start. Outside
a box `ENV` is unset and `bind` is the system's.
"""

from __future__ import annotations

import errno
import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
from pathlib import Path

__all__ = ["ENV", "bind", "install", "window"]

ENV = "POOLHOUSE_LOOPBACK_PORTS"
"""``<first>-<last>@<socket>``: the ports of this box's window and the Unix socket that binds one of them."""
HOSTS = {socket.AF_INET: ("127.0.0.1", "localhost"), socket.AF_INET6: ("::1", "localhost")}
ANSWER_S = 10.0
_SYSTEM = socket.socket.bind


def window() -> tuple[range, str] | None:
    """The window of this process and the socket that serves it; None where there is none."""
    ports, _, path = os.environ.get(ENV, "").partition("@")
    first, _, last = ports.partition("-")
    if not (path and first.isdigit() and last.isdigit()):
        return None
    return range(int(first), int(last) + 1), path


def bind(sock: socket.socket, address: object, system=None) -> None:
    """``sock.bind(address)``; in a box, a loopback TCP port 0 or a port of the window is bound by the launcher and put in ``sock``'s place."""
    system = system or _SYSTEM
    found = window()
    if (found is None or sock.family not in HOSTS or sock.type != socket.SOCK_STREAM or not isinstance(address, tuple)
            or address[0] not in HOSTS[sock.family] or (address[1] != 0 and address[1] not in found[0])):
        system(sock, address)
        return
    asked = {"family": 6 if sock.family == socket.AF_INET6 else 4, "port": address[1],
             "reuse": bool(sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR))}
    with socket.socket(socket.AF_UNIX) as bridge:
        bridge.settimeout(ANSWER_S)
        bridge.connect(found[1])
        bridge.sendall(json.dumps(asked).encode() + b"\n")
        said, handed, _, _ = socket.recv_fds(bridge, 128, 1)
        if handed:
            bridge.sendall(b"k")   # the launcher keeps its own descriptor until this one has arrived
    if not handed:
        number = json.loads(said or b"{}").get("errno", errno.EPERM)
        raise OSError(number, os.strerror(number))
    timeout, inheritable = sock.gettimeout(), sock.get_inheritable()
    try:
        os.dup2(handed[0], sock.fileno(), inheritable=inheritable)
    finally:
        os.close(handed[0])
    sock.settimeout(timeout)


def install() -> None:
    """Make `socket.socket.bind` of this process go through `bind`, once; nothing where there is no window."""
    current = socket.socket.bind
    if window() is None or getattr(current, "loopback_window", False):
        return

    def bound(self: socket.socket, address: object) -> None:
        bind(self, address, current)

    bound.loopback_window = True  # type: ignore[attr-defined]
    socket.socket.bind = bound  # type: ignore[method-assign]


def _shadowed() -> None:
    """Run the ``sitecustomize`` this file stands in front of (Homebrew's Python has one), so the interpreter starts as it always does."""
    here = Path(__file__).resolve().parent
    spec = importlib.machinery.PathFinder.find_spec(__name__, [each for each in sys.path if each and Path(each).resolve() != here])
    if spec is not None and spec.loader is not None:
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


if __name__ == "sitecustomize":
    install()
    _shadowed()

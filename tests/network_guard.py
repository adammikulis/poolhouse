"""No test touches the network beyond loopback, or opens something on the person's screen.

macOS raises a Local Network prompt for the first process that binds a non-loopback address, joins a
multicast group or sends to the LAN, and a dialog or a browser window for the first ``osascript``,
``open`` or ``security`` call. A test never means to, and a variable somebody has to remember to set
is not a guard, so every test runs under this one (`conftest.py` imports it) and it fails the test
that tripped it, at its end, even when the code under test swallowed the refusal.

A test that must listen on the network carries ``@pytest.mark.lan_network`` and runs only when
``POOLHOUSE_LAN_TESTS=1`` is set by a person at the machine (`live.MARKERS`).
"""

from __future__ import annotations

import contextlib
import errno
import inspect
import ipaddress
import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from poolhouse.testing import live

OPENERS = frozenset({"open", "osascript", "security", "xdg-open", "notify-send", "zenity", "kdialog", "x-terminal-emulator",
                     "gnome-terminal", "konsole", "xterm", "firefox", "google chrome", "google-chrome", "chromium",
                     "chromium-browser", "chrome", "safari", "msedge", "microsoft edge", "cmd.exe", "explorer.exe",
                     "rundll32.exe", "powershell.exe", "wscript.exe", "cscript.exe", "mshta.exe"})
"""Programs that put a window, a prompt or a keystore question in front of a person."""

JOINS = tuple(getattr(socket, name) for name in ("IP_ADD_MEMBERSHIP", "IPV6_JOIN_GROUP") if hasattr(socket, name))


def loopback(host: object) -> bool:
    """Whether ``host`` is this machine alone: 127.0.0.0/8, ::1 or localhost. The empty name and 0.0.0.0 are not."""
    if not isinstance(host, str):
        return False
    text = host.strip("[]").split("%")[0]
    if text == "localhost":
        return True
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)


def opener(argv: object) -> str:
    """The name of the desktop program ``argv`` would start from outside the temporary directory, or ''."""
    if isinstance(argv, (str, bytes, os.PathLike)):
        argv = os.fsdecode(argv).split()
    first = os.fsdecode(argv[0]) if argv else ""
    if not first:
        return ""
    found = shutil.which(first) or first
    name = Path(first).name.lower()
    if name not in OPENERS and not name.endswith(".app"):
        return ""
    with contextlib.suppress(OSError, ValueError):
        Path(found).resolve().relative_to(Path(tempfile.gettempdir()).resolve())
        return ""  # a stand-in the test made itself, or the suite's own shims, which record the attempt
    return name


def call_site() -> str:
    """The nearest ``file:line`` in a test file, else the nearest outside this file and the standard library's socket code."""
    frames = [f for f in inspect.stack()[1:] if f.filename != __file__]
    skip = (f"{os.sep}socket.py", f"{os.sep}socketserver.py", f"{os.sep}subprocess.py")
    for frame in frames:
        if f"{os.sep}tests{os.sep}" in frame.filename:
            return f"{frame.filename}:{frame.lineno}"
    return next((f"{f.filename}:{f.lineno}" for f in frames if not f.filename.endswith(skip)), "<unknown call site>")


def internet(address: object, family: int) -> bool:
    """Whether ``address`` is on this machine's network beyond loopback (the wildcard, a LAN address, multicast, broadcast).

    A public host is left to `_no_public_network` in ``conftest.py``, which says why and honours ``live_net``."""
    return (family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and bool(address)
            and not loopback(address[0]) and not live.outside(address[0]))


@contextlib.contextmanager
def watching() -> Iterator[list[str]]:
    """Refuse (and list) every non-loopback bind, multicast join, send beyond loopback and desktop program start."""
    seen: list[str] = []
    real = {"bind": socket.socket.bind, "connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex,
            "sendto": socket.socket.sendto, "setsockopt": socket.socket.setsockopt, "popen": subprocess.Popen.__init__,
            "system": os.system}

    def refuse(what: str) -> None:
        seen.append(f"{what} at {call_site()}")
        raise PermissionError(errno.EACCES, f"a test may not {what}; mark it lan_network and run it with POOLHOUSE_LAN_TESTS=1")

    def bind(self, address):
        if internet(address, self.family):
            refuse(f"bind {address[0]!r}")
        return real["bind"](self, address)

    def reach(name):
        def go(self, address):
            if internet(address, self.family) and self.type != socket.SOCK_DGRAM:
                refuse(f"connect to {address[0]!r}")
            return real[name](self, address)
        return go

    def sendto(self, data, *rest):
        if internet(rest[-1], self.family):
            refuse(f"send to {rest[-1][0]!r}")
        return real["sendto"](self, data, *rest)

    def setsockopt(self, level, option, *value):
        if option in JOINS and level in (socket.IPPROTO_IP, getattr(socket, "IPPROTO_IPV6", -1)):
            refuse("join a multicast group")
        return real["setsockopt"](self, level, option, *value)

    def popen(self, args, *more, **named):
        if name := opener(args):
            refuse(f"start {name}")
        return real["popen"](self, args, *more, **named)

    def system(command):
        if name := opener(command):
            refuse(f"start {name}")
        return real["system"](command)

    socket.socket.bind, socket.socket.connect, socket.socket.connect_ex = bind, reach("connect"), reach("connect_ex")
    socket.socket.sendto, socket.socket.setsockopt = sendto, setsockopt
    subprocess.Popen.__init__, os.system = popen, system
    try:
        yield seen
    finally:
        socket.socket.bind, socket.socket.connect, socket.socket.connect_ex = real["bind"], real["connect"], real["connect_ex"]
        socket.socket.sendto, socket.socket.setsockopt = real["sendto"], real["setsockopt"]
        subprocess.Popen.__init__, os.system = real["popen"], real["system"]


@pytest.fixture(autouse=True)
def nothing_beyond_loopback(request):
    """Run the test under `watching`, and fail it when anything was refused."""
    if request.node.get_closest_marker("lan_network"):
        yield
        return
    with watching() as seen:
        yield
    if seen:
        pytest.fail("a test reached beyond this machine or opened something on screen:\n" + "\n".join(seen), pytrace=False)

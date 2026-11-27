"""The box's window of loopback ports, used and attacked from inside the real confinement.

The launch is the real one (`tests.test_redteam_isolation_shell.shelled`: a shell script that is NOT A MODEL and NOT A HARNESS, and the visibly
marked plumbing server). A program of the box serves on loopback through the launcher; it binds no port itself, so it can listen on no other
address, and it reaches no listener outside its window.
"""

from __future__ import annotations

import os
import shlex
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from poolhouse.isolation import ports as windows
from poolhouse.sandbox import Net, Policy, seatbelt as profiles
from tests.test_redteam_isolation_shell import box, shelled  # noqa: F401  (box is a fixture)

pytest_plugins = ["tests.confined_kit", "tests.sandbox_kit"]
pytestmark = pytest.mark.usefixtures("confinable", "seatbelt")

SERVES = """
import socket, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class Said(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Length", "6"); self.end_headers(); self.wfile.write(b"served")
    def log_message(self, *a): pass
import urllib.request
server = ThreadingHTTPServer(("127.0.0.1", 0), Said)
threading.Thread(target=server.serve_forever, daemon=True).start()
print("address", server.server_address[0], server.server_port)
print("answer", urllib.request.urlopen("http://127.0.0.1:%d/" % server.server_port, timeout=5).read().decode())
server.shutdown(); server.server_close()
with socket.create_server(("::1", 0), family=socket.AF_INET6) as six, socket.create_connection(("::1", six.getsockname()[1]), 5):
    six.settimeout(5); six.accept()[0].close(); print("six served")
chosen = socket.socket(); chosen.bind(("127.0.0.1", 0)); port = chosen.getsockname()[1]; chosen.close()
child = ("import socket,sys; s=socket.create_server(('127.0.0.1', int(sys.argv[1]))); print('up', flush=True); "
         "c,_=s.accept(); c.sendall(b'from the child')")
held = subprocess.Popen([sys.executable, "-c", child, str(port)], stdout=subprocess.PIPE)
held.stdout.readline()
print(socket.create_connection(("127.0.0.1", port), 5).recv(64).decode()); held.wait(10)
"""

ATTACK = """
import _socket, ctypes, json, os, socket, sys
ports, path = os.environ["POOLHOUSE_LOOPBACK_PORTS"].split("@")
first, last = (int(each) for each in ports.split("-"))
def tried(name, call):
    try:
        call(); print("OPEN", name)
    except OSError:
        print("shut", name)
def raw(host, port, kind=_socket.SOCK_STREAM, listen=True):
    s = _socket.socket(_socket.AF_INET, kind); s.bind((host, port))
    if listen: s.listen(1)
for host in ("127.0.0.1", "0.0.0.0"):
    tried("own bind of a window port on " + host, lambda: raw(host, first + 1))
    tried("own udp bind on " + host, lambda: raw(host, first + 1, _socket.SOCK_DGRAM, False))
tried("wildcard through the patched socket", lambda: socket.socket().bind(("0.0.0.0", 0)))
tried("a port outside the window", lambda: socket.socket().bind(("127.0.0.1", 8080)))
tried("a listen with no bind", lambda: _socket.socket().listen(1))
libc = ctypes.CDLL(None, use_errno=True)
class Addr(ctypes.Structure):
    _fields_ = [("len", ctypes.c_uint8), ("family", ctypes.c_uint8), ("port", ctypes.c_uint16), ("addr", ctypes.c_uint8 * 4), ("zero", ctypes.c_uint8 * 8)]
class Ends(ctypes.Structure):
    _fields_ = [("srcif", ctypes.c_uint), ("src", ctypes.c_void_p), ("srclen", ctypes.c_uint32), ("dst", ctypes.c_void_p), ("dstlen", ctypes.c_uint32)]
def chosen_source(kind, flags):
    s = socket.socket(socket.AF_INET, kind)
    src = Addr(16, 2, socket.htons(first + 2), (ctypes.c_uint8 * 4)(0, 0, 0, 0))
    dst = Addr(16, 2, socket.htons(first + 3), (ctypes.c_uint8 * 4)(127, 0, 0, 1))
    ends = Ends(0, ctypes.addressof(src), 16, ctypes.addressof(dst), 16)
    libc.connectx(s.fileno(), ctypes.byref(ends), 0, flags, None, 0, None, None)
    return s
for flags in (0, 1):
    tried("a listen after a connect that names its source %d" % flags, lambda: chosen_source(socket.SOCK_STREAM, flags).listen(1))
def datagram():
    s = chosen_source(socket.SOCK_DGRAM, 0)
    libc.connect(s.fileno(), ctypes.byref(Addr(16, 0, 0, (ctypes.c_uint8 * 4)(0, 0, 0, 0))), 16)
    t = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); t.sendto(b"x", ("127.0.0.1", first + 2)); s.settimeout(1); s.recv(1)
tried("a datagram received or sent on a window port", datagram)
def asked(request):
    with socket.socket(socket.AF_UNIX) as bridge:
        bridge.settimeout(5); bridge.connect(path); bridge.sendall(request)
        said, handed, _, _ = socket.recv_fds(bridge, 128, 1)
    if not handed:
        raise OSError(said)
for request in (b'{"family": 4, "port": 8080, "reuse": true}\\n', b'{"family": 4, "port": %d, "reuse": true}\\n' % (last + 1), b'not a request\\n'):
    tried("the bridge asked for " + request.decode().strip(), lambda: asked(request))
for name in ("early", "late"):
    open(name + ".wanted", "w").close()
    import time
    while not os.path.exists(name + ".port"):
        time.sleep(0.05)
    port = int(open(name + ".port").read())
    for host in ("127.0.0.1", "::1"):
        tried("the %s listener outside the window at %s" % (name, host),
              lambda: socket.create_connection((host, port), 3))
tried("a host beyond this machine on a window port", lambda: socket.create_connection(("192.0.2.1", first), 3))
print("first", first, "last", last)
"""


def test_a_program_of_the_box_serves_on_loopback_and_its_children_do_too(box):  # noqa: F811
    (box.project / "serves.py").write_text(SERVES)
    out, _, _ = shelled(box, f"{shlex.quote(os.path.realpath(sys.executable))} serves.py\n")
    for expected in ("address 127.0.0.1 ", "answer served", "six served", "from the child"):
        assert expected in out, (expected, out)
    port = int(out.split("address 127.0.0.1 ", 1)[1].split()[0])
    assert windows.FIRST <= port <= windows.LAST, out


def test_the_box_listens_on_no_other_address_and_reaches_no_listener_outside_its_window_even_one_started_later(box):  # noqa: F811
    (box.project / "attack.py").write_text(ATTACK)
    listeners: list[socket.socket] = []
    watching: list[threading.Thread] = []
    heard: list[str] = []

    def listen(name: str, stage: Path) -> None:
        deadline = time.monotonic() + 60
        while not (stage / f"{name}.wanted").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        four = socket.create_server(("127.0.0.1", 0))
        port = four.getsockname()[1]
        six = socket.create_server(("::1", port), family=socket.AF_INET6)
        listeners.extend((four, six))
        for each in (four, six):
            each.settimeout(30)
            threading.Thread(target=_hear, args=(each, name, heard), daemon=True).start()
        (stage / f"{name}.port").write_text(str(port))

    def look(confinement) -> None:
        if not watching:
            stage = Path(confinement.session.stage.physical)
            for name in ("early", "late"):   # both start after the box's profile is written and proved; "late" after the box has run attacks
                watching.append(threading.Thread(target=listen, args=(name, stage), daemon=True))
                watching[-1].start()

    try:
        out, _, confinement = shelled(box, f"{shlex.quote(os.path.realpath(sys.executable))} attack.py\n", look=look)
        assert "OPEN " not in out and out.count("shut ") == 18 and heard == [], (heard, out)
        first, last = (int(out.split(f"{word} ", 1)[1].split()[0]) for word in ("first", "last"))
        assert (first, last) == (confinement.profile.policy.net.accept[0], confinement.profile.policy.net.accept[-1])
        assert all(int(each.getsockname()[1]) not in range(first, last + 1) for each in listeners)
    finally:
        for each in listeners:
            each.close()


def _hear(listener: socket.socket, name: str, heard: list[str]) -> None:
    try:
        listener.accept()[0].close()
        heard.append(name)
    except OSError:
        pass


def test_a_window_holds_no_port_in_use_or_fixed_and_no_two_launches_share_one(tmp_path):
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        assert held.getsockname()[1] in windows.in_use()
    locks = tmp_path / "locks"
    roots = [tempfile.mkdtemp(prefix="pp-", dir="/tmp") for _ in range(2)]
    every = range(windows.FIRST, windows.LAST + 1)
    left = range(windows.FIRST, windows.FIRST + 2 * windows.SIZE)
    first, second = (windows.PortBridge(root, locks, avoid=set(every) - set(left)) for root in roots)
    try:
        first.start()
        second.start()
        assert not set(first.ports) & set(second.ports) and set(first.ports) | set(second.ports) <= set(left)
        with pytest.raises(OSError, match="in use"):
            windows.PortBridge(roots[0], locks, avoid=set(every) - set(left)).start()
    finally:
        first.stop()
        second.stop()
        for root in roots:
            Path(root).rmdir()
    fixed = {7447, 7448, 8770, 8773, 8794, *range(8080, 8100)}
    assert not fixed & set(every) and windows.LAST < 49152


def test_the_profile_refuses_every_bind_of_a_port_it_serves_on_after_allowing_the_traffic():
    policy = Policy("t", net=Net.only(5000, accept=(41000, 41001)), strict=True)
    text = profiles.profile(policy, "/bin/echo", "t")
    deny = '(deny network-bind (local ip "localhost:41000") (local ip "localhost:41001"))'
    assert deny in text and text.index(deny) > text.index('(allow network-inbound (local tcp "localhost:41000")'), text
    assert 'network-bind (local ip "localhost:5000")' not in text


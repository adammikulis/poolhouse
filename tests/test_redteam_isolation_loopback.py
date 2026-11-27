"""The box's window of loopback ports, used and attacked from inside the real confinement.

The launch is the real one (`tests.test_redteam_isolation_shell.shelled`: a shell script that is NOT A MODEL and NOT A HARNESS, and the visibly
marked plumbing server). A program of the box serves on loopback through the launcher; it binds no port itself, so it can listen on no other
address, and it reaches no listener outside its window.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

import poolhouse
from poolhouse import loopback_window
from poolhouse.isolation import ports as windows
from poolhouse.isolation.capability import lan_address
from poolhouse.platform import start_process
from poolhouse.sandbox import Net, Policy, policies, seatbelt as profiles
from poolhouse.serve import free_port
from poolhouse.serve.confined import confine
from poolhouse.serve.process import kill_process_tree
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


BINDS = """
import _socket, json
named = json.load(open("named.json"))
def tried(name, call):
    s = None
    try:
        call()
    except PermissionError:
        print("shut", name)
    except OSError as why:
        print("OPEN", name, why.errno)   # the system took the bind up: the box did not refuse it
    else:
        print("OPEN", name)
def bound(family, kind, host, port):
    s = _socket.socket(family, kind)
    try:
        s.bind((host, port))
        if kind == _socket.SOCK_STREAM:
            s.listen(1)
    finally:
        s.close()
def unbound():
    s = _socket.socket()
    try:
        s.listen(1)
    finally:
        s.close()
hosts = [(_socket.AF_INET, "127.0.0.1"), (_socket.AF_INET, "0.0.0.0"), (_socket.AF_INET6, "::1"), (_socket.AF_INET6, "::"),
         *((_socket.AF_INET, each) for each in named["own"])]
for port in [0, *named["ports"]]:
    for family, host in hosts:
        for kind in (_socket.SOCK_STREAM, _socket.SOCK_DGRAM):
            tried("%s port %d kind %d" % (host, port, kind), lambda: bound(family, kind, host, port))
tried("a listen with no bind", unbound)
print("tried", len(hosts) * 2 * (1 + len(named["ports"])) + 1)
"""

LISTENS = """
import _socket, json
for port in json.load(open("named.json"))["ports"]:
    s = _socket.socket()
    try:
        s.bind(("0.0.0.0", port))
        s.listen(1)
    except OSError:
        s.close()
        continue
    open("listening-%d" % port, "w").close()
    s.settimeout(10)
    try:
        taken, _ = s._accept()
        _socket.close(taken)
        print("REACHED", port)
    except OSError:
        print("unreached", port)
    finally:
        s.close()
print("done")
"""


def _named(confinement, box, own: list[str]) -> Path:  # noqa: F811
    """Every port this launch names, written into the stage for the program of the box: the harness server's, the model bridge's, those a harness
    may listen on and the ends of the window."""
    net = confinement.profile.policy.net
    ports = sorted({*box.servers, *net.ports, *getattr(net, "listen", ()), *net.accept[:1], *net.accept[-1:]})
    stage = Path(confinement.session.stage.physical)
    (stage / "named.json").write_text(json.dumps({"ports": ports, "own": own}))
    return stage


def test_no_program_of_a_harness_box_binds_or_listens_on_any_address_not_even_on_the_ports_its_launch_names(box):  # noqa: F811
    (box.project / "binds.py").write_text(BINDS)
    own = [address] if (address := lan_address()) else []

    def look(confinement) -> None:
        _named(confinement, box, own)

    out, _, _ = shelled(box, f"{shlex.quote(os.path.realpath(sys.executable))} binds.py\n", look=look, brief="PLUMBING")
    assert box.servers, "the launch is the one OpenCode gets"
    opened = [line for line in out.splitlines() if line.startswith("OPEN ")]
    assert not opened and f"tried {out.count('shut ')}\n" in out, (opened, out)


@pytest.mark.parametrize("source", ["loopback", pytest.param("network", marks=pytest.mark.lan_network)])
def test_a_wildcard_listener_started_in_a_harness_box_is_never_reached(box, source):  # noqa: F811
    """From this machine's own network address (a person runs that one, at the machine) and over loopback. Nothing is sent: a connection that is
    taken is closed at once."""
    address = lan_address() if source == "network" else "127.0.0.1"
    assert address, "this computer has no address on a network, so nothing could reach the box from it"
    (box.project / "listens.py").write_text(LISTENS)
    reached: list[int] = []
    ended = threading.Event()
    watching: list[threading.Thread] = []

    def connect(stage: Path) -> None:
        while not ended.wait(0.02):
            for flag in stage.glob("listening-*"):
                port = int(flag.name.split("-")[1])
                if port not in reached:
                    with contextlib.suppress(OSError), socket.create_connection((address, port), 3):
                        reached.append(port)

    def look(confinement) -> None:
        if not watching:
            watching.append(threading.Thread(target=connect, args=(_named(confinement, box, []),), daemon=True))
            watching[0].start()

    try:
        out, _, _ = shelled(box, f"{shlex.quote(os.path.realpath(sys.executable))} listens.py\n", look=look, brief="PLUMBING")
    finally:
        ended.set()
    assert "done" in out and "REACHED" not in out and not reached, (reached, out)


STAND_IN = """
# NOT A MODEL and NOT A MODEL SERVER: it serves where it is told to, as llama-server does (a host ending `.sock` is a Unix socket), says whether it
# could bind every address, and marks its one answer.
import socket, sys
host, port = sys.argv[sys.argv.index("--host") + 1], int(sys.argv[sys.argv.index("--port") + 1])
try:
    socket.socket().bind(("0.0.0.0", 0))
    everywhere = "OPEN"
except PermissionError:
    everywhere = "shut"
if host.endswith(".sock"):
    server = socket.socket(socket.AF_UNIX)
    server.bind(host)
else:
    server = socket.socket()
    server.bind((host, port))
server.listen(8)
while True:
    connection = server.accept()[0]
    connection.recv(65536)
    body = b"[CANNED RESPONSE, NOT A MODEL] wildcard=" + everywhere.encode()
    connection.sendall(b"HTTP/1.1 200 OK\\r\\nConnection: close\\r\\nContent-Length: %d\\r\\n\\r\\n%s" % (len(body), body))
    connection.close()
"""


def test_a_confined_model_server_is_reached_on_loopback_and_binds_no_address_itself(tmp_path):
    program = tmp_path / "server-NOT-A-MODEL.py"
    program.write_text(STAND_IN)
    interpreter = os.path.realpath(sys.executable)
    port = free_port()
    runtime = [f"--runtime={tree}" for tree in policies.runtime_reads()]   # named on its command line, so the box reads what this stand-in runs on
    held = confine([interpreter, str(program), "--host", "127.0.0.1", "--port", str(port), *runtime], {}, Path(interpreter))
    source = str(Path(poolhouse.__file__).resolve().parents[1])   # the relay runs outside the box and imports this tree
    process = start_process(held.argv, env={**held.env, "PYTHONPATH": source}, cwd=held.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    said = b""
    try:
        deadline = time.monotonic() + 30
        while not said and time.monotonic() < deadline and process.poll() is None:
            with contextlib.suppress(OSError, http.client.HTTPException):
                asked = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                asked.request("GET", "/health")
                said = asked.getresponse().read()
                asked.close()
            time.sleep(0.05)
    finally:
        kill_process_tree(process.pid)
        output = process.communicate(timeout=10)[0]
        held.backend.finish(held.tag)
    assert said == b"[CANNED RESPONSE, NOT A MODEL] wildcard=shut", (said, output, held.argv)


def test_a_socket_the_launcher_hands_over_takes_its_first_connection_every_time(tmp_path, monkeypatch):
    """macOS breaks a socket now and then (about one in eight) when its sender closes it while it is still in flight."""
    root = tempfile.mkdtemp(prefix="pp-", dir="/tmp")
    bridge = windows.PortBridge(root, tmp_path / "locks")
    bridge.start()
    monkeypatch.setenv(loopback_window.ENV, bridge.setting)
    broken: list[str] = []
    try:
        for _ in range(200):
            with socket.socket() as server:
                loopback_window.bind(server, ("127.0.0.1", 0))
                server.listen(5)
                server.settimeout(5)
                try:
                    with socket.create_connection(("127.0.0.1", server.getsockname()[1]), 5) as client, server.accept()[0] as taken:
                        client.sendall(b"ask")
                        taken.settimeout(5)
                        if taken.recv(3) != b"ask":
                            broken.append("closed")
                except OSError as why:
                    broken.append(type(why).__name__)
    finally:
        bridge.stop()
        Path(root).rmdir()
    assert not broken, (len(broken), sorted(set(broken)))


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


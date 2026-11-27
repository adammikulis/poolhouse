"""A launcher pointed with ``--on`` at a server of this computer: the server's key goes to it only when this computer can show the process there
is the one it started and keyed, and a server that needs a key it is not given is refused with the reason.

PLUMBING ONLY: no model is served. Each server is a marked stand-in (`poolhouse.testing.canned`, or a listener here that records what it was
sent), on a real loopback port.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from test_serve_refused_key import started

from poolhouse import claude, harnessing, home, serverkeys
from poolhouse.files import write_json
from poolhouse.serve.leases import lease_file
from poolhouse.serve.ports import free_port
from poolhouse.serve.process import cmdline_digest, started_at
from poolhouse.testing.registry import record_server

CANNED = "canned-model"


@pytest.fixture
def processes():
    made: list[subprocess.Popen] = []
    yield made
    for one in made:
        one.kill()
        one.wait()


class _Listener(BaseHTTPRequestHandler):
    """NOT A MODEL: answers the model list to anybody and keeps the credential of every request."""

    def do_GET(self):
        self.server.heard.append(self.headers.get("Authorization"))
        body = json.dumps({"data": [{"id": CANNED}]}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


class _SixListener(ThreadingHTTPServer):
    address_family = socket.AF_INET6


@pytest.fixture
def listeners():
    made = []

    def listen(host: str = "127.0.0.1", port: int = 0):
        server = (_SixListener if ":" in host else ThreadingHTTPServer)((host, port), _Listener)
        server.heard = []
        threading.Thread(target=server.serve_forever, daemon=True).start()
        made.append(server)
        return server

    yield listen
    for server in made:
        server.shutdown()
        server.server_close()


def launched(url: str):
    """What a launcher does with ``--on url``: its exit status, its lines and what the harness would be started on."""
    lines: list[str] = []
    ran: list[tuple[str, str, int]] = []
    status = harnessing.serve_and_run(SimpleNamespace(on=url), "opencode", lines.append, claude.alias_of, lambda served: ran.append(served) or 0)
    return status, "\n".join(lines), ran


def keyed(port: int, pid: int, **record) -> str:
    """A key of this computer for ``port`` bound to ``pid``, and the lease record of that process serving it."""
    key = serverkeys.issue(port)
    serverkeys.bind(port, pid)
    record_server(lease_file(), port, **{"model": "m.gguf", "pid": pid, "owner_pid": pid, "started": started_at(pid),
                                         "cmdline": cmdline_digest(pid), **record})
    return key


def ours(processes, tmp_path):
    """A keyed stand-in server this computer started: its port and key."""
    port = free_port()
    key = serverkeys.issue(port)
    up = started(processes, tmp_path / "qwen.gguf", key, port)
    serverkeys.bind(port, up.process.pid)
    record_server(lease_file(), port, model="qwen.gguf", pid=up.process.pid, owner_pid=up.process.pid, started=started_at(up.process.pid),
                  cmdline=cmdline_digest(up.process.pid))
    return port, key


def sleeper(processes) -> int:
    processes.append(subprocess.Popen(["sleep", "120"]))
    return processes[-1].pid


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_a_server_this_computer_started_is_used_with_its_key_at_the_address_it_listens_on(processes, tmp_path, host):
    port, _key = ours(processes, tmp_path)
    status, said, ran = launched(f"http://{host}:{port}/")
    assert status == 0, said
    assert ran[0][0] == f"http://127.0.0.1:{port}" and ran[0][1], "the key goes to the one address the server holds, however the person wrote it"


@pytest.mark.redteam
@pytest.mark.parametrize("case", ["another-process-took-the-port", "the-pid-was-reused", "no-lease-record", "an-unmanaged-record"])
def test_a_listener_this_computer_cannot_show_is_its_server_is_sent_no_key(processes, listeners, case):
    listener = listeners()
    port = listener.server_port
    if case == "another-process-took-the-port":
        keyed(port, sleeper(processes))
    elif case == "the-pid-was-reused":
        keyed(port, os.getpid(), started=1.0)
    elif case == "no-lease-record":
        serverkeys.issue(port)
        serverkeys.bind(port, os.getpid())
    else:
        keyed(port, os.getpid(), unmanaged=True)
    status, said, ran = launched(f"http://127.0.0.1:{port}")
    assert listener.heard == [], "nothing was asked of it, so no key reached it"
    assert status == 2 and ran == [] and "no key is sent to" in said and str(port) in said, said


@pytest.mark.redteam
def test_the_key_never_goes_to_the_other_loopback_address_another_process_holds(processes, tmp_path, listeners):
    port, key = ours(processes, tmp_path)
    other = listeners("::1", port)
    status, said, ran = launched(f"http://[::1]:{port}")
    assert other.heard == [] and status == 2 and ran == [], (other.heard, said)
    assert "does not listen at" in said, said
    status, said, ran = launched(f"http://localhost:{port}")
    assert status == 0 and ran[0][0] == f"http://127.0.0.1:{port}" and f"Bearer {key}" not in other.heard, (other.heard, said)


@pytest.mark.redteam
@pytest.mark.parametrize("written", ["http://elsewhere.invalid\\@127.0.0.1:{port}", "http://someone@127.0.0.1:{port}",
                                     "http://127.0.0.1:{port}?to=elsewhere", "https://127.0.0.1:{port}"])
def test_an_address_two_programs_could_read_differently_is_given_no_key_even_for_our_server(processes, tmp_path, written):
    port, key = ours(processes, tmp_path)
    status, said, ran = launched(written.format(port=port))
    assert status == 2 and ran == [] and "no key is sent to" in said and key not in said, said


def test_a_server_whose_key_this_computer_does_not_hold_is_refused_with_why(processes, tmp_path):
    """A server an earlier build left up keeps its key where this build does not read: the launch says so rather than that it names no model."""
    port = free_port()
    up = started(processes, tmp_path / "qwen.gguf", "the-key-an-earlier-build-gave-it", port)
    folder = home.state("server-keys.d")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{port}.key").write_text("the-key-an-earlier-build-gave-it\n", encoding="utf-8")
    write_json(home.state("server-keys.json"), {str(port): {"pid": up.process.pid}})
    record_server(lease_file(), port, model="qwen.gguf", pid=up.process.pid, owner_pid=up.process.pid, started=started_at(up.process.pid),
                  cmdline=cmdline_digest(up.process.pid))
    status, said, ran = launched(f"http://127.0.0.1:{port}")
    assert status == 2 and ran == [], said
    assert "refuses a request without its key" in said and "no key this computer holds is bound" in said and "poolhouse-serve" in said, said


def test_a_server_that_asks_for_no_key_and_is_not_ours_is_still_used(listeners):
    listener = listeners()
    status, said, ran = launched(f"http://127.0.0.1:{listener.server_port}")
    assert status == 0 and ran[0][1] == CANNED and set(listener.heard) == {None}, said

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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from test_serve_refused_key import started

from poolhouse import claude, codex, harnessid, harnessing, home, opencode, pi, serverkeys
from poolhouse.client.health import reported_models
from poolhouse.files import read_json, write_json
from poolhouse.http import Cancellation, build_request, cancel_scope, head_once
from poolhouse.ownership import cmdline_digest
from poolhouse.serve import backend as be
from poolhouse.serve.grant import broker_grant
from poolhouse.serve.leases import lease_file
from poolhouse.serve.policy import LocalPolicy
from poolhouse.serve.ports import free_port
from poolhouse.serve.process import started_at
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

    def do_HEAD(self):
        self.server.heard.append(self.headers.get("Authorization"))
        self.send_response(200)
        self.end_headers()

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


@pytest.fixture
def proxy(listeners, monkeypatch):
    """An HTTP proxy the environment names, with no exception for this computer: it keeps what it is sent."""
    held = listeners()
    monkeypatch.setattr(urllib.request, "_opener", None)   # as in a new process: the library's own opener reads the proxies when it is first made
    for name in ("NO_PROXY", "no_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, f"http://127.0.0.1:{held.server_port}")
    return held


@pytest.mark.redteam
@pytest.mark.parametrize("case", [(False, ""), (True, ""), (False, "localhost"), (True, "localhost")],
                         ids=["plain", "cancellable", "plain-with-localhost-excepted", "cancellable-with-localhost-excepted"])
def test_a_request_to_this_computer_never_goes_through_a_proxy_of_the_environment(processes, tmp_path, proxy, monkeypatch, case):
    cancellable, excepted = case
    port, _key = ours(processes, tmp_path)
    if excepted:
        monkeypatch.setenv("NO_PROXY", excepted)
    if cancellable:
        with cancel_scope(Cancellation()):
            assert reported_models(f"http://127.0.0.1:{port}")
    else:
        status, said, ran = launched(f"http://localhost:{port}")
        assert status == 0 and ran[0][1], said
    assert proxy.heard == [], "the proxy was asked, with the server's key"


@pytest.mark.redteam
@pytest.mark.parametrize("harness", [codex, pi, opencode], ids=["codex", "pi", "opencode"])
def test_a_harness_run_in_place_is_told_to_reach_this_computer_with_no_proxy(processes, tmp_path, proxy, monkeypatch, harness):
    port, _key = ours(processes, tmp_path)
    monkeypatch.setenv("NO_PROXY", "held.example")
    monkeypatch.setattr(harness, "alias_of", lambda *_: "m")
    monkeypatch.setattr(harnessing, "window_of", lambda _: 0)
    ran, said, name = [], [], harness.__name__.rsplit(".", 1)[-1]
    assert harness.launch(["--on", f"http://127.0.0.1:{port}", "--project", str(tmp_path), f"--{name}", "true", "--", "word"], say=said.append,
                          session_factory=lambda made, *_: harnessid.HarnessSession(made),
                          **{f"run_{name}": lambda run: ran.append(run) or 0}) == 0, said
    for variable in ("NO_PROXY", "no_proxy"):
        excepted = ran[0].env[variable].split(",")
        assert {"held.example", "127.0.0.1", "localhost", "::1"} <= set(excepted), ran[0].env[variable]


@pytest.mark.redteam
@pytest.mark.parametrize("case", ["another-process-listens", "the-pid-was-reused", "bound-with-no-start-time"])
def test_a_key_is_attached_only_when_its_own_process_listens_at_the_address(processes, case):
    held = socket.create_server(("127.0.0.1", 0))
    port = held.getsockname()[1]
    serverkeys.issue(port)
    serverkeys.bind(port, sleeper(processes) if case == "another-process-listens" else os.getpid())
    if case in ("the-pid-was-reused", "bound-with-no-start-time"):
        entries = read_json(home.state("server-keys.json"), {})
        entries[str(port)]["started"] = 1.0
        if case == "bound-with-no-start-time":
            del entries[str(port)]["started"]
        write_json(home.state("server-keys.json"), entries)
    with held:
        assert serverkeys.for_url(f"http://127.0.0.1:{port}/props") == ""
        assert build_request(f"http://127.0.0.1:{port}/props").get_header("Authorization") is None


def test_a_request_written_to_localhost_is_sent_with_the_key_to_the_address_the_server_holds(processes, tmp_path):
    port, key = ours(processes, tmp_path)
    request = build_request(f"http://localhost:{port}/props")
    assert (request.full_url, request.get_header("Authorization")) == (f"http://127.0.0.1:{port}/props", f"Bearer {key}")


@pytest.mark.redteam
@pytest.mark.parametrize("written", ["http://[::1", "http://127.0.0.1:abc", "http://127.0.0.1:99999", "http://127.0.0.1:8080/a b", "nothing"])
def test_an_address_that_cannot_be_asked_is_one_sentence_and_no_traceback(written):
    status, said, ran = launched(written)
    assert status == 2 and ran == [] and "--on" in said and "is not an http address" in said, said


def test_a_start_on_a_port_a_keyed_server_holds_leaves_that_servers_key(processes, tmp_path):
    port, key = ours(processes, tmp_path)
    weights, binary = tmp_path / "other.gguf", tmp_path / "llama-server"
    weights.write_bytes(b"GGUF")
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    with broker_grant(), pytest.raises(be.ServerFailed, match="held by a process"):
        be.LlamaServerBackend(binary=binary, sandboxed=False, policy=LocalPolicy()).start(
            be.ServerSpec(model=weights, port=port), lease=be.Lease(port=port, owner_pid=os.getpid(), state_file=str(lease_file())),
            check_flags=False, preflight=False)
    assert serverkeys.for_url(f"http://127.0.0.1:{port}") == key


@pytest.mark.redteam
def test_a_head_request_to_this_computer_goes_through_no_proxy(listeners, proxy):
    server = listeners()
    key = keyed(server.server_port, os.getpid())
    assert head_once(f"http://127.0.0.1:{server.server_port}/v1/models").status == 200
    assert (proxy.heard, server.heard) == ([], [f"Bearer {key}"])


@pytest.mark.parametrize("known_by", ["the-key-record", "the-lease-record"])
def test_a_key_whose_number_another_process_took_does_not_hold_the_port(listeners, known_by):
    """A number used again: nothing of this computer's server is there, so a server that asks for no key is used."""
    listener = listeners()
    port = listener.server_port
    keyed(port, os.getpid(), started=1.0)
    entries = read_json(home.state("server-keys.json"), {})
    if known_by == "the-key-record":
        entries[str(port)]["started"] = 1.0
    else:   # as the previous build wrote it: only the lease record tells when that server started
        del entries[str(port)]["started"]
    write_json(home.state("server-keys.json"), entries)
    status, said, ran = launched(f"http://127.0.0.1:{port}")
    assert status == 0 and ran[0][1] == CANNED and set(listener.heard) == {None}, said

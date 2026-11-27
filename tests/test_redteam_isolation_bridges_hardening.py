"""The model bridge, the egress proxy, the session bridge and the proof, attacked after their own review (docs/notes/attack-verdict-isolation-l2-l3.md,
M2 to M6, M8, L1, L3, L4, L5 and L7). Every request is written to a real socket of a real service; the upstream is the plumbing server of the bridge
tests, which keeps what it is sent and says in every reply that it is not a model. A refusal is immediate and fixed in words.
"""

# ruff: noqa: F811  (the fixtures of the bridge tests are imported by name and asked for by name)
from __future__ import annotations

import errno
import importlib
import json
import os
import resource
import select
import socket
import threading
import time
from pathlib import Path

import pytest

from poolhouse.guard import fetchlog
from poolhouse.isolation import (
    Handlers,
    SessionBridge,
    Terms,
    capability,
    egress,
    proof,
)
from poolhouse.isolation.egress_proxy import blocked
from poolhouse.isolation.serving import Server
from poolhouse.testing.canned import CANNED_MARKER
from tests.isolation_kit import world
from tests.test_redteam_isolation_bridge import (  # noqa: F401  (the fixtures and helpers of the bridge tests)
    ALIAS,
    BODY,
    LEASE,
    PUBLIC,
    Sink,
    Upstream,
    asked,
    basic,
    bridge,
    connect,
    heard,
    held,
    opening,
    proxied,
    said,
    sent,
    status_of,
    talk,
    until,
    upstream,
)

pytest_plugins = ["tests.sandbox_kit"]


# M3: the model bridge forwards what it names and nothing else


def test_the_model_list_is_the_leases_own_and_never_the_servers(bridge, upstream):
    answer = talk(bridge.port, asked("GET", "/v1/models", bridge.token))
    assert status_of(answer) == 200 and upstream.requests == [], "the server was not asked"
    listed = json.loads(answer.partition(b"\r\n\r\n")[2])
    assert [each["id"] for each in listed["data"]] == [ALIAS] and listed["data"][0]["context_window"] == LEASE.window
    assert set(listed["data"][0]) <= {"id", "object", "type", "display_name", "owned_by", "context_window", "created"}, "no path, size or account"


def test_only_the_fields_a_route_takes_reach_the_server_and_a_slot_a_cache_switch_or_an_adapter_do_not(bridge, upstream):
    sent = {"model": ALIAS, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 5, "slot_id": 3, "id_slot": 3, "cache_prompt": False,
            "lora": [{"id": 0, "scale": 1}], "grammar": "root ::= x", "n_predict": 99999, "logit_bias": {"1": 9}, "return_tokens": True,
            "samplers": ["top_k"], "timings_per_token": True, "response_fields": ["x"], "unknown": "x"}
    assert status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, json.dumps(sent).encode()))) == 200
    assert set(upstream.body()) == {"model", "messages", "max_tokens"}, set(upstream.body())


@pytest.mark.parametrize("parts", [
    [{"type": "image_url", "image_url": {"url": "http://169.254.169.254/latest/meta-data/"}}],
    [{"type": "image_url", "image_url": "https://example.test/x.png"}],
    [{"type": "image_url", "image_url": {"url": "file:///etc/passwd"}}],
    [{"type": "image", "source": {"type": "url", "url": "http://127.0.0.1:9/x"}}],
    [{"type": "image", "source": {"type": "file", "file_id": "file_1"}}],
    [{"type": "input_audio", "url": "http://127.0.0.1:9/a.wav"}],
    [{"type": "file", "file_id": "file-abc"}],
    [{"type": "tool_result", "tool_use_id": "t", "content": [{"type": "image_url", "image_url": {"url": "ftp://x/y"}}]}]])
def test_a_picture_or_a_file_named_by_an_address_is_refused_and_never_reaches_the_server(bridge, upstream, parts):
    body = json.dumps({"model": ALIAS, "messages": [{"role": "user", "content": parts}]}).encode()
    answer = talk(bridge.port, asked("POST", "/v1/chat/completions", bridge.token, body))
    assert status_of(answer) == 400 and b"data: URL" in answer and upstream.requests == [], answer[:200]


def test_a_picture_sent_as_its_own_bytes_and_a_tool_input_that_holds_a_url_pass(bridge, upstream):
    parts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
             {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="}},
             {"type": "tool_use", "id": "t", "name": "WebFetch", "input": {"url": "https://example.test/page"}}]
    body = json.dumps({"model": ALIAS, "messages": [{"role": "user", "content": parts}]}).encode()
    assert status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, body))) == 200 and len(upstream.requests) == 1


def test_a_busy_model_makes_a_request_wait_its_turn_and_nobody_is_asked(held):
    """A wait is for a slot of the server and never for a person: nothing is answered, refused or sent on while it lasts, and it ends by itself."""
    first = sent(held.bridge.port, held.ask("first"))
    assert held.server.entered.wait(5)
    second = sent(held.bridge.port, held.ask("second"))
    assert until(lambda: held.bridge.busy() == 2)
    assert not select.select([second], [], [], 2.5)[0] and said(held.server) == ["first"], "no answer yet, and the server has only what it has a slot for"
    held.hold.set()
    answer = heard(second)
    assert status_of(answer) == 200 and CANNED_MARKER.encode() in answer, answer[:300]
    assert status_of(heard(first)) == 200 and held.bridge.summary()["refused"] == 0


def test_a_descriptor_beyond_the_range_of_select_does_not_stop_a_model_bridge_answer(bridge, upstream):
    with high_descriptors():
        assert status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, BODY))) == 200


class high_descriptors:
    """Hold enough open descriptors that the next socket has a number past 1024, the limit of `select`."""

    def __enter__(self):
        self.soft, self.hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (min(self.hard if self.hard > 0 else 8192, 8192), self.hard))
        self.held = []
        while True:
            fd = os.open(os.devnull, os.O_RDONLY)
            self.held.append(fd)
            if fd > 1100:
                return self

    def __exit__(self, *_):
        for fd in self.held:
            os.close(fd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (self.soft, self.hard))


# L3, L1, L7 and M6: the egress proxy answers its own session, blocks the wrapped forms, survives descriptors and budgets a session


def test_a_caller_without_the_sessions_credential_gets_one_fixed_407_and_the_sentinel_hears_nothing():
    sink = Sink()
    heard = []
    made = proxied(sink, {"pypi.org": [PUBLIC]}, events=lambda name, fields: heard.append(name))
    try:
        for extra in ("", "Proxy-Authorization: Basic AAAA\r\n", f"Proxy-Authorization: {basic('x' * 43)}\r\n", "Authorization: " + basic(made.token) + "\r\n"):
            answer = talk(made.port, f"CONNECT pypi.org:443 HTTP/1.1\r\nHost: pypi.org:443\r\n{extra}\r\n".encode())
            assert status_of(answer) == 407 and b'Proxy-Authenticate: Basic realm="poolhouse"' in answer, (extra, answer[:200])
        for target in ("evil.example:443", "127.0.0.1:443", "pypi.org:80"):
            assert status_of(talk(made.port, f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())) == 407, "no refusal tells who is not authenticated"
        assert not made.dialled and sink.accepted == 0 and heard == [] and made.counts()["pypi.org"]["refused"] == 0
        assert status_of(connect(made.port, "pypi.org:443")) == 200, "the control: the session's own credential is served"
        assert made.token not in repr(made) and made.token in made.proxy_url and made.proxy_url.startswith("http://poolhouse:")
        doubled = f"Proxy-Authorization: {basic(made.token)}\r\nProxy-Authorization: {basic(made.token)}\r\n"
        assert status_of(connect(made.port, "pypi.org:443", extra=doubled)) == 400
    finally:
        made.stop()
        sink.close()


@pytest.mark.parametrize("address", ["::127.0.0.1", "::10.0.0.1", "::192.168.1.1", "::a00:1", "::ffff:0:10.0.0.1", "::ffff:0:7f00:1", "::ffff:10.0.0.1",
                                     "64:ff9b::10.0.0.1", "2002:0a00:0001::", "::1", "::"])
def test_every_ipv6_form_that_carries_an_ipv4_address_is_blocked_whole(address):
    assert blocked(address), address
    assert not blocked("2606:4700:4700::1111") and not blocked(PUBLIC), "a public address of either family is not"


def test_a_tunnel_runs_with_descriptors_past_the_range_of_select_and_the_accept_loop_survives_what_a_listener_fails_with():
    sink = Sink()
    made = proxied(sink, {"pypi.org": [PUBLIC]})
    try:
        with high_descriptors():
            with socket.create_connection(("127.0.0.1", made.port), timeout=5) as sock:
                sock.sendall(opening(made))
                assert sock.recv(4096).startswith(b"HTTP/1.1 200")
                sock.sendall(b"x" * 100)
                time.sleep(0.3)
            time.sleep(0.2)
        assert sink.received == 100 and made.counts()["pypi.org"]["out"] == 100
    finally:
        made.stop()
        sink.close()
    flaky = Flaky()
    served: list[bytes] = []
    stopped: list[int] = []
    server = Server(socket.AF_INET, ("127.0.0.1", 0), lambda connection: served.append(connection.recv(5)), stopped=lambda: stopped.append(1))
    server._listener = flaky   # the service's own listener, wrapped so that accept fails as it does when descriptors run out
    thread = threading.Thread(target=server._accept, args=(flaky,), daemon=True)
    thread.start()
    with socket.create_connection(flaky.real.getsockname(), timeout=5) as sock:
        sock.sendall(b"hello")
        time.sleep(0.5)
    assert served == [b"hello"] and stopped == [], "two EMFILE and an ECONNABORTED did not end the service"
    flaky.fail = [errno.EBADF]
    with socket.create_connection(flaky.real.getsockname(), timeout=5) as sock:   # wakes the loop that waits in accept, which then fails for good
        sock.sendall(b"again")
    thread.join(5)
    assert stopped == [1] and not thread.is_alive(), "a listener that is gone is said to the sentinel, once"


class Flaky:
    """A listener whose accept fails as it does when the process is out of descriptors, and then serves as the real one."""

    def __init__(self) -> None:
        self.real = socket.socket()
        self.real.bind(("127.0.0.1", 0))
        self.real.listen(4)
        self.fail = [errno.EMFILE, errno.EMFILE, errno.ECONNABORTED]

    def accept(self):
        if self.fail:
            raise OSError(self.fail.pop(0), "injected")
        return self.real.accept()


def test_a_request_head_that_drips_is_closed_at_its_total_deadline_not_held_by_each_byte():
    sink = Sink()
    made = proxied(sink, {"pypi.org": [PUBLIC]})
    made.idle = 1.0
    try:
        began = time.monotonic()
        with socket.create_connection(("127.0.0.1", made.port), timeout=10) as sock:
            try:
                for each in b"CONNECT pypi.org:443 HTTP/1.1\r\nHost: pypi.org:443\r\n":
                    sock.sendall(bytes([each]))
                    time.sleep(0.15)
            except OSError:
                pass
            got = bytearray()
            sock.settimeout(5)
            try:
                while more := sock.recv(4096):
                    got.extend(more)
            except OSError:
                pass
        assert time.monotonic() - began < 4 and not made.dialled and sink.accepted == 0, "a drip of one byte at a time held it open"
    finally:
        made.stop()
        sink.close()


def test_a_second_normal_install_is_not_cut_and_the_session_budget_ends_the_session_at_a_fixed_sentence():
    tunnel, session = egress_numbers()
    sink = Sink()
    made = proxied(sink, {"pypi.org": [PUBLIC]})
    try:
        for _ in range(2):
            with socket.create_connection(("127.0.0.1", made.port), timeout=5) as sock:
                sock.sendall(opening(made))
                assert sock.recv(4096).startswith(b"HTTP/1.1 200")
                sock.sendall(b"y" * (session // 3))
                time.sleep(0.15)
        time.sleep(0.3)
        assert sink.received == 2 * (session // 3) and made.counts()["pypi.org"]["cut"] == 0, "two installs of a third of the budget each are not cut"
        with socket.create_connection(("127.0.0.1", made.port), timeout=5) as sock:
            sock.sendall(opening(made))
            assert sock.recv(4096).startswith(b"HTTP/1.1 200")
            sock.sendall(b"z" * (tunnel * 3))
            time.sleep(0.3)
        assert sink.received == session and made.counts()["pypi.org"]["out"] == session, "never more than the budget over every tunnel, and all of it"
        answer = connect(made.port, "pypi.org:443")
        assert status_of(answer) == 403 and b"budget of this coding session" in answer, answer[:200]
    finally:
        made.stop()
        sink.close()


def egress_numbers() -> tuple[int, int]:
    from tests.isolation_kit import REGISTRY
    return REGISTRY["pypi.org"].bytes_out, REGISTRY["pypi.org"].session_bytes


def test_the_registry_records_a_bound_for_one_tunnel_and_a_budget_for_a_session_each_with_its_measurement():
    for each in egress.hosts().values():
        assert each.session_bytes >= each.bytes_out > 0 and each.command and each.date, each
    listed = json.loads((Path(egress.__file__).parents[3] / "contracts" / "egress-hosts.json").read_text())
    for raw in listed["hosts"]:
        assert raw["measured_session"]["bytes_out"] + raw["measured"]["bytes_out"] <= raw["session_bytes_out_bound"], raw["host"]
        assert raw["measured"]["max_tunnel_out"] <= raw["bytes_out_bound"], raw["host"]


# M4, M5, L4 and M8: the session bridge is bounded, says nothing of the parent, admits by rule and takes only plain names


class Recorder:
    """A workspace and a hook that note what they are asked, and fail as a test tells them to."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.logged: list[tuple[str, dict]] = []
        self.heard: list[tuple[str, dict]] = []
        self.fail: BaseException | None = None
        self.inside = 0
        self.most_inside = 0
        self.gate = threading.Event()
        self.gate.set()
        self.lock = threading.Lock()

    def hook(self, event: str, payload: dict) -> dict:
        with self.lock:
            self.inside += 1
            self.most_inside = max(self.most_inside, self.inside)
        try:
            self.gate.wait(5)
            if self.fail is not None:
                raise self.fail
            return {"event": event}
        finally:
            with self.lock:
                self.inside -= 1

    def workspace(self, name: str, arguments: dict) -> object:
        self.calls.append((name, dict(arguments)))
        if self.fail is not None:
            raise self.fail
        return {"ok": True}

    def handlers(self) -> Handlers:
        return Handlers(hook=self.hook, workspace=self.workspace, log=lambda kind, **fields: self.logged.append((kind, fields)),
                        events=lambda name, fields: self.heard.append((name, fields)))


@pytest.fixture
def recorded():
    made = Recorder()
    bridges: list[SessionBridge] = []

    def build(window: int = 4096, **kw):
        kw.setdefault("unadmitted", "a test of the bridge's own frames")
        found = SessionBridge(made.handlers(), Terms(window), root="/tmp", idle=2.0, **kw)
        found.start()
        bridges.append(found)
        return found

    made.build = build
    yield made
    for each in bridges:
        each.stop()


def exchange(path: str, frames: list[bytes]) -> list[dict]:
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(10)
        sock.connect(path)
        reader = sock.makefile("rb")
        out = []
        for each in frames:
            sock.sendall(each + b"\n")
            line = reader.readline()
            if not line:
                break
            out.append(json.loads(line))
        return out


def test_a_frame_with_a_non_finite_number_never_reaches_the_workspace_and_a_hostile_frame_never_ends_the_connection(recorded):
    made = recorded.build()
    for bad in (b'{"verb":"workspace","name":"note","arguments":{"text":Infinity}}', b'{"verb":"workspace","name":"note","arguments":{"text":NaN}}'):
        assert "refused" in exchange(made.path, [bad])[0] and recorded.calls == []
    recorded.fail = OverflowError("int too large to convert")
    survived = exchange(made.path, [json.dumps({"verb": "hook", "event": "pre", "payload": {}}).encode(), json.dumps({"verb": "tools"}).encode()])
    assert len(survived) == 2 and "refused" in survived[0] and "tools" in survived[1], "the connection answered the frame after the one that raised"


def test_a_session_sends_no_more_frames_in_a_minute_than_its_budget_and_the_rest_are_answered_with_one_fixed_sentence(recorded):
    made = recorded.build()
    answers = exchange(made.path, [json.dumps({"verb": "tools"}).encode()] * (made.budget + 20))
    assert len(answers) == made.budget + 20 and sum("tools" in each for each in answers) == made.budget
    busy = [each for each in answers if "refused" in each]
    assert len(busy) == 20 and {each["refused"] for each in busy} == {"That is more frames than a session sends in a minute."}
    assert made.summary()["refused"]["busy"] == 20 and made.budget >= 60


def test_one_notification_runs_at_a_time_and_the_rest_are_answered_as_the_last_one_was(recorded):
    made = recorded.build()
    recorded.gate.clear()
    answers: list[dict] = []
    frame = json.dumps({"verb": "hook", "event": "post", "payload": {}}).encode()
    threads = [threading.Thread(target=lambda: answers.extend(exchange(made.path, [frame]))) for _ in range(6)]
    [each.start() for each in threads]
    time.sleep(0.5)
    recorded.gate.set()
    [each.join(10) for each in threads]
    assert len(answers) == 6 and recorded.most_inside == 1, f"{recorded.most_inside} notifications ran at once"


def test_a_failure_of_the_workspace_tells_the_session_one_fixed_sentence_and_nothing_the_parent_holds(recorded):
    made = recorded.build()
    recorded.fail = FileNotFoundError(2, "No such file", "/Users/someone/.poolhouse/node/client/demo/lead.token")
    answer = exchange(made.path, [json.dumps({"verb": "workspace", "name": "note", "arguments": {"text": "t"}}).encode()])[0]
    assert answer == {"error": "The workspace could not run note (FileNotFoundError)."}, answer
    assert "someone" not in json.dumps(answer) and ".poolhouse" not in json.dumps(answer)
    assert any(kind == "isolation.workspace_failed" and "lead.token" in fields["meta"]["detail"] for kind, fields in recorded.logged), "the detail is logged"


def test_a_bridge_admits_by_a_rule_or_says_why_it_has_none(tmp_path, recorded):
    with pytest.raises(ValueError, match="admit predicate"):
        SessionBridge(recorded.handlers(), Terms(4096), root="/tmp")
    made = recorded.build(unadmitted="a test of a bridge with no rule")
    assert any(kind == "isolation.session_bridge_unadmitted" and fields["meta"]["reason"] == "a test of a bridge with no rule"
               for kind, fields in recorded.logged), "the reason is logged where a person can read it"
    ruled = recorded.build(admit=lambda pid: False, unadmitted="")
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(3)
        sock.connect(ruled.path)
        sock.sendall(b'{"verb":"tools"}\n')
        assert sock.recv(4096) == b"", "a process the rule refuses is closed at once"
    assert made.path != ruled.path


# M2: the fetch log is one file for one name and does not grow without end


def test_two_names_that_fold_to_one_readable_form_have_two_logs_and_a_log_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path))
    fetchlog.note("a/b", ["/tmp/one"])
    fetchlog.note("a_b", ["/tmp/two"])
    assert fetchlog.read("a/b") == ["/tmp/one"] and fetchlog.read("a_b") == ["/tmp/two"]
    for number in range(60):
        fetchlog.note("flood", [f"/tmp/{number}-{each}-" + "x" * 480 for each in range(50)])
    size = fetchlog._file("flood").stat().st_size
    assert size <= fetchlog.MOST_BYTES + 50 * 520 and len(fetchlog.read("flood")) <= fetchlog.MOST, size


# L5: a proof that could not listen on a network says so and is never taken for a pass


def test_a_computer_with_no_address_on_a_network_reports_the_lan_attempt_as_not_checked_and_never_as_passed(tmp_path, monkeypatch):
    report_module = importlib.import_module("poolhouse.isolation.capability")
    monkeypatch.setattr(report_module, "lan_address", lambda: "")
    monkeypatch.setattr("poolhouse.isolation.proof.lan_address", lambda: "")
    report_module.drop_cache()
    try:
        assert capability().unchecked == ("P08",) and capability().to_dict()["unchecked"] == ["P08"]
        with world(tmp_path) as w:
            done = proof.run(w.profile, w.services)
        line = next(each for each in done.lines if each.id == "P08")
        assert line.observed.startswith("not checked") and done.unchecked == ("P08",), line
        assert [each.id for each in done.lines if each.observed.startswith("not checked")] == ["P08"], "nothing else is reported as unchecked"
    finally:
        report_module.drop_cache()

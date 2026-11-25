"""The model bridge and the egress proxy against real attacks: routes, smuggling, a second model, the server's key, SSRF and a stage-sized upload.

Every request here is written to a real socket of a real bridge or proxy. The upstream the model bridge forwards to is a plumbing server that
records the bytes it is sent and says in every reply that it is not a model (`poolhouse.testing.canned`, `CANNED-NOT-A-MODEL`); no model behaviour is
under test. The one thing replaced beside it is the resolver and the dial of the egress proxy, so that no connection leaves the machine: an
address the resolver names is dialled as a listener of this test, and a listener that must never be reached counts what reaches it
(docs/design-isolated-launcher.md, 6.1, 6.3, 13.2, A27 to A29, AF5).
"""

from __future__ import annotations

import ast
import base64
import contextlib
import dataclasses
import http.client
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from poolhouse.isolation import (
    EgressProxy,
    IsolationRefused,
    Lease,
    ModelBridge,
    Wiring,
    egress,
    egress_proxy,
    model_bridge,
    wire,
)
from poolhouse.isolation.egress_proxy import blocked
from poolhouse.sandbox import Net, run
from poolhouse.testing.canned import (
    CANNED_HEADER,
    CANNED_HEADER_VALUE,
    CANNED_MARKER,
    CANNED_MODEL_ID,
    Canned,
    CannedServer,
)
from tests.isolation_kit import REGISTRY, SOURCE
from tests.sandbox_kit import policy

ALIAS = CANNED_MODEL_ID
LEASE = Lease(ALIAS, 4096, 1)
KEY = "the-server-key-that-never-leaves-the-parent"
BODY = json.dumps({"model": ALIAS, "messages": [{"role": "user", "content": "hello"}], "stream": False}).encode()
FIELDS_SENT_UPSTREAM = {"host", "connection", "content-type", "accept", "anthropic-version", "user-agent", "authorization", "content-length"}


class Upstream:
    """A plumbing server that is not a model: it keeps every request it is sent, byte for byte, and answers 200 with the canned marker and two
    response fields (a cookie and a secret) the bridge must not pass on. With ``hold`` set it does not answer until the event is set."""

    def __init__(self, hold: threading.Event | None = None) -> None:
        self.requests: list[bytes] = []
        self.hold = hold
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.port = self.sock.getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.entered = threading.Event()
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(5)
            held = bytearray()
            try:
                while b"\r\n\r\n" not in held:
                    held.extend(connection.recv(65536))
                head, _, rest = bytes(held).partition(b"\r\n\r\n")
                length = next((int(line.split(b":")[1]) for line in head.split(b"\r\n") if line.lower().startswith(b"content-length")), 0)
                while len(rest) < length:
                    rest += connection.recv(65536)
            except (OSError, ValueError):
                return
            self.requests.append(head + b"\r\n\r\n" + rest)
            self.entered.set()
            if self.hold is not None:
                self.hold.wait(10)
            body = json.dumps({"x_canned": CANNED_MARKER, "model": CANNED_MODEL_ID}).encode()
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode()
                               + b"\r\n" + f"{CANNED_HEADER}: {CANNED_HEADER_VALUE}\r\n".encode()
                               + b"Set-Cookie: stolen=1\r\nX-Server-Secret: leaked\r\n\r\n" + body)

    def close(self) -> None:
        self.sock.close()

    def fields(self, at: int = 0) -> dict[str, str]:
        head = self.requests[at].partition(b"\r\n\r\n")[0].decode("latin-1").split("\r\n")[1:]
        return {line.partition(":")[0].lower(): line.partition(":")[2].strip() for line in head}

    def body(self, at: int = 0) -> dict:
        return json.loads(self.requests[at].partition(b"\r\n\r\n")[2])


def talk(port: int, data: bytes, *, patience: float = 5.0) -> bytes:
    """Everything the service at ``port`` answers to ``data`` until it closes the connection."""
    with socket.create_connection(("127.0.0.1", port), timeout=patience) as sock:
        sock.sendall(data)
        got = bytearray()
        try:
            while more := sock.recv(65536):
                got.extend(more)
        except (TimeoutError, ConnectionResetError):
            pass
        return bytes(got)


def status_of(answer: bytes) -> int:
    return int(answer.split(b" ", 2)[1]) if answer.startswith(b"HTTP/1.1 ") else 0


def asked(method: str, target: str, token: str, body: bytes = b"", *fields: str, auth: bool = True) -> bytes:
    """One well-formed request, with the extra header ``fields`` after the usual ones."""
    lines = [f"{method} {target} HTTP/1.1", "Host: 127.0.0.1", *([f"Authorization: Bearer {token}"] if auth else []),
             "Content-Type: application/json", f"Content-Length: {len(body)}", *fields]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


@pytest.fixture
def upstream():
    server = Upstream()
    yield server
    server.close()


@pytest.fixture
def bridge(upstream):
    events: list[tuple[str, dict]] = []
    made = ModelBridge(upstream.base_url, LEASE, key=KEY, events=lambda name, fields: events.append((name, fields)), idle=1.0)
    made.heard = events
    made.start()
    yield made
    made.stop()


def test_the_model_bridge_serves_only_its_routes_and_pins_the_model(bridge, upstream):
    for method, target in (("POST", "/v1/messages"), ("POST", "/v1/messages?beta=true"), ("POST", "/v1/messages/count_tokens"),
                           ("POST", "/v1/chat/completions"), ("POST", "/v1/responses"), ("GET", "/v1/models")):
        body = BODY if method == "POST" else b""
        assert status_of(talk(bridge.port, asked(method, target, bridge.token, body))) == 200, (method, target)
    assert [first.split(b" ")[:2] for first in (each.split(b"\r\n")[0] for each in upstream.requests)] == [
        [b"POST", b"/v1/messages"], [b"POST", b"/v1/messages"], [b"POST", b"/v1/messages/count_tokens"], [b"POST", b"/v1/chat/completions"],
        [b"POST", b"/v1/responses"]], "the table's routes reach the server (the model list is the bridge's own), and a query does not"


@pytest.mark.parametrize("method,target", [
    ("GET", "/slots"), ("POST", "/slots/0?action=save"), ("GET", "/props"), ("POST", "/props"), ("GET", "/tools"), ("GET", "/metrics"),
    ("GET", "/lora-adapters"), ("POST", "/lora-adapters"), ("POST", "/v1/embeddings"), ("POST", "/embedding"), ("POST", "/infill"),
    ("POST", "/completion"), ("GET", "/"), ("GET", "/models"), ("POST", "/models/load"), ("GET", "/v1/messages"), ("POST", "/v1/models"),
    ("DELETE", "/v1/messages"), ("PUT", "/v1/chat/completions"), ("OPTIONS", "/v1/messages"), ("POST", "/v1/messages/"),
    ("POST", "/V1/messages"), ("POST", "/health")])
def test_every_other_route_is_404_here_and_never_reaches_the_server(bridge, upstream, method, target):
    answer = talk(bridge.port, asked(method, target, bridge.token, BODY if method == "POST" else b""))
    assert status_of(answer) == 404, answer[:200]
    assert not upstream.requests, "the server heard nothing of it"
    assert any(name == "isolation.probe" for name, _ in bridge.heard), "a refused route is told to the sentinel as a probe"
    assert bridge.summary()["refused"] >= 1


def test_health_is_answered_by_the_bridge_without_the_server_and_without_a_slot():
    hold = threading.Event()
    server = Upstream(hold)
    made = ModelBridge(server.base_url, LEASE, key=KEY, idle=5.0)
    made.start()
    try:
        taken = threading.Thread(target=talk, args=(made.port, asked("POST", "/v1/messages", made.token, BODY)), daemon=True)
        taken.start()
        assert server.entered.wait(5), "the one slot is in use"
        assert status_of(talk(made.port, asked("GET", "/health", made.token, auth=False))) == 200
        assert len(server.requests) == 1, "the health route never reached the server"
        assert status_of(talk(made.port, asked("POST", "/v1/messages", made.token, BODY))) == 429, "a second request is refused, not queued"
        hold.set()
        taken.join(10)
        assert status_of(talk(made.port, asked("POST", "/v1/messages", made.token, BODY))) == 200, "the slot is free again"
    finally:
        hold.set()
        made.stop()
        server.close()


def _forms(token: str) -> dict[str, tuple[bytes, set[int]]]:
    """Requests that two parsers could read differently, and requests that are not plain: each must be refused before the server hears it."""
    ok = ["Host: 127.0.0.1", f"Authorization: Bearer {token}", "Content-Type: application/json"]

    def raw(line: str, *fields: str, body: bytes = BODY, length: bool = True) -> bytes:
        held = [line, *fields, *([f"Content-Length: {len(body)}"] if length else [])]
        return ("\r\n".join(held) + "\r\n\r\n").encode() + body

    post = "POST /v1/messages HTTP/1.1"
    return {
        "absolute-form": (raw("POST http://127.0.0.1:9/v1/messages HTTP/1.1", *ok), {400}),
        "connect": (raw("CONNECT 127.0.0.1:9 HTTP/1.1", *ok), {400, 404}),
        "double slash": (raw("POST //v1/messages HTTP/1.1", *ok), {400}),
        "double slash inside": (raw("POST /v1//messages HTTP/1.1", *ok), {400}),
        "dot dot": (raw("POST /v1/../v1/messages HTTP/1.1", *ok), {400}),
        "escaped dot": (raw("POST /v1/%2e%2e/v1/messages HTTP/1.1", *ok), {400}),
        "escaped dot upper": (raw("POST /v1/%2E./messages HTTP/1.1", *ok), {400}),
        "escaped slash": (raw("POST /v1%2fmessages HTTP/1.1", *ok), {400}),
        "escaped backslash": (raw("POST /v1%5cmessages HTTP/1.1", *ok), {400}),
        "escaped nul": (raw("POST /v1/messages%00.x HTTP/1.1", *ok), {400}),
        "backslash": (raw("POST /v1\\messages HTTP/1.1", *ok), {400}),
        "fragment": (raw("POST /v1/messages#x HTTP/1.1", *ok), {400}),
        "upgrade": (raw(post, *ok, "Upgrade: websocket", "Connection: Upgrade"), {400}),
        "expect": (raw(post, *ok, "Expect: 100-continue"), {400}),
        "content-length and transfer-encoding": (raw(post, *ok, "Transfer-Encoding: chunked"), {400}),
        "transfer-encoding alone": (raw(post, *ok, "Transfer-Encoding: chunked", body=b"5\r\nhello\r\n0\r\n\r\n", length=False), {400}),
        "duplicate content-length": (raw(post, *ok, "Content-Length: 3"), {400}),
        "duplicate content-length same": (raw(post, *ok, f"Content-Length: {len(BODY)}"), {400}),
        "signed length": (raw(post, *ok, body=BODY, length=False).replace(b"\r\n\r\n", b"\r\nContent-Length: +5\r\n\r\n", 1), {400}),
        "hex length": (raw(post, *ok, body=BODY, length=False).replace(b"\r\n\r\n", b"\r\nContent-Length: 0x10\r\n\r\n", 1), {400}),
        "huge length": (raw(post, *ok, body=BODY, length=False).replace(b"\r\n\r\n", b"\r\nContent-Length: 99999999999\r\n\r\n", 1), {413}),
        "folded header": (raw(post, *ok, "X-A: b", " continued: Authorization: Bearer x"), {400}),
        "bare line feed": (raw(post, *ok).replace(b"\r\n", b"\n"), {400, 408}),
        "nul in a value": (raw(post, *ok, "User-Agent: a\x00b"), {400}),
        "space before colon": (raw(post, *ok, "X-A : b"), {400}),
        "two hosts": (raw(post, *ok, "Host: 127.0.0.1"), {400}),
        "no host": (raw(post, *ok[1:]), {400}),
        "http 1.0": (raw("POST /v1/messages HTTP/1.0", *ok), {400}),
        "http 2 preface": (b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n", {400}),
        "two authorization fields": (raw(post, *ok, f"Authorization: Bearer {token}"), {400}),
        "no length on a post": (raw(post, *ok, length=False), {411}),
        "oversize head": (raw(post, *ok, "X-Fill: " + "a" * 70000), {431}),
        "too many fields": (raw(post, *ok, *[f"X-{n}: a" for n in range(120)]), {431}),
    }


@pytest.mark.parametrize("name", list(_forms("t")))
def test_a_request_that_is_not_plain_is_refused_before_the_server_hears_it(bridge, upstream, name):
    data, wanted = _forms(bridge.token)[name]
    answer = talk(bridge.port, data)
    assert status_of(answer) in wanted, (name, answer[:160])
    assert not upstream.requests, f"{name}: the server heard it"
    assert b"stolen" not in answer and KEY.encode() not in answer


def test_a_second_request_behind_the_first_is_never_read_or_forwarded(bridge, upstream):
    first = asked("POST", "/v1/messages", bridge.token, BODY)
    second = asked("POST", "/v1/chat/completions", bridge.token, BODY)
    answer = talk(bridge.port, first + second)
    assert answer.count(b"HTTP/1.1 ") == 1 and status_of(answer) == 200
    assert len(upstream.requests) == 1 and upstream.requests[0].startswith(b"POST /v1/messages HTTP/1.1")


def test_a_request_hidden_in_a_body_is_a_body_and_nothing_else(bridge, upstream):
    hidden = asked("POST", "/slots/0?action=erase", bridge.token, b"{}")
    answer = talk(bridge.port, asked("POST", "/v1/messages", bridge.token, hidden))
    assert status_of(answer) == 400 and not upstream.requests


def test_the_servers_key_is_added_and_the_sessions_token_never_reaches_the_server(upstream):
    server = CannedServer(Canned(), port=0)
    server.api_key = KEY
    made = ModelBridge(server.base_url, LEASE, key=KEY, idle=2.0)
    made.start()
    try:
        answer = talk(made.port, asked("POST", "/v1/chat/completions", made.token, BODY))
        assert status_of(answer) == 200, "the server demands its key, and the bridge brought it"
        assert CANNED_MARKER.encode() in answer, "the reply says that it is not a model"
        direct = http.client.HTTPConnection("127.0.0.1", server.port)
        direct.request("POST", "/v1/chat/completions", body=BODY, headers={"Authorization": f"Bearer {made.token}", "Content-Type": "application/json"})
        assert direct.getresponse().status == 401, "the session's token is worth nothing to the server itself"
        direct.close()
    finally:
        made.stop()
        server.close()
    sent = ModelBridge(upstream.base_url, LEASE, key=KEY, idle=2.0)
    sent.start()
    try:
        talk(sent.port, asked("POST", "/v1/messages", sent.token, BODY, f"X-Api-Key: {sent.token}"))
        talk(sent.port, asked("POST", "/v1/messages", "x", BODY, f"X-Api-Key: {sent.token}", auth=False))
    finally:
        sent.stop()
    assert len(upstream.requests) == 2
    for each in upstream.requests:
        assert sent.token.encode() not in each and f"Bearer {KEY}".encode() in each


def test_a_request_with_no_token_or_another_token_is_refused_and_a_wrong_field_beside_a_right_one_too(bridge, upstream):
    for fields, auth in (((), False), (("Authorization: Bearer nope",), False), (("X-Api-Key: nope",), False), (("Authorization: Basic abc",), False),
                         ((f"X-Api-Key: {bridge.token}", "Authorization: Bearer nope"), False), (("Authorization: Bearer",), False)):
        assert status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, BODY, *fields, auth=auth))) == 401, fields
    assert status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, BODY, f"X-Api-Key: {bridge.token}", auth=False))) == 200
    assert len(upstream.requests) == 1


def test_only_four_request_fields_reach_the_server_and_none_of_the_responses_fields_but_the_framing(bridge, upstream):
    fields = ("Accept: application/json", "Anthropic-Version: 2023-06-01", "User-Agent: harness/1", "Cookie: session=1", "X-Forwarded-For: 10.0.0.1",
              "X-Evil: 1", "Anthropic-Beta: x", "Accept-Encoding: gzip", "Proxy-Authorization: Basic abc", "Origin: http://evil.example")
    answer = talk(bridge.port, asked("POST", "/v1/messages", bridge.token, BODY, *fields))
    assert status_of(answer) == 200
    assert set(upstream.fields()) == FIELDS_SENT_UPSTREAM
    assert upstream.fields()["user-agent"] == "harness/1" and upstream.fields()["authorization"] == f"Bearer {KEY}"
    head = answer.partition(b"\r\n\r\n")[0].lower()
    assert b"set-cookie" not in head and b"x-server-secret" not in head
    assert b"content-type: application/json" in head and b"connection: close" in head


def test_the_model_is_pinned_and_the_slot_and_adapter_fields_are_removed(bridge, upstream):
    sent = [{"messages": [], "model": ""}, {"messages": []}, {"messages": [], "model": ALIAS, "id_slot": 3, "lora": [{"id": 0, "scale": 1}], "n": 1}]
    for each in sent:
        assert status_of(talk(bridge.port, asked("POST", "/v1/chat/completions", bridge.token, json.dumps(each).encode()))) == 200
    assert [upstream.body(n)["model"] for n in range(3)] == [ALIAS] * 3
    assert all("id_slot" not in upstream.body(n) and "lora" not in upstream.body(n) for n in range(3))
    assert upstream.body(2)["n"] == 1 and upstream.body(2)["messages"] == [], "the rest of the body is the session's own"
    count = len(upstream.requests)
    for other in ("qwen-other", "CANNED-NOT-A-MODEL ", "canned-not-a-model", None, 7, ["CANNED-NOT-A-MODEL"]):
        answer = talk(bridge.port, asked("POST", "/v1/messages", bridge.token, json.dumps({"model": other, "messages": []}).encode()))
        assert status_of(answer) == 400, other
        assert b"qwen" not in answer, "a refusal never repeats what the session sent"
    assert len(upstream.requests) == count


@pytest.mark.parametrize("body", [b'{"model":"other","model":"CANNED-NOT-A-MODEL"}', b'{"model":"CANNED-NOT-A-MODEL","model":"other"}',
                                  b'{"model":"CANNED-NOT-A-MODEL","mo\\u0064el":"other"}', b'{"a":{"x":1,"x":2},"model":""}', b'{"model":"",}',
                                  b'{"model":"","n":NaN}', b'{"model":"","n":Infinity}', b'[1]', b'"text"', b'', b'{"model":"\\ud800"}',
                                  b'[' * 100000 + b']' * 100000])
def test_a_body_that_is_not_one_json_object_with_each_field_once_is_refused(bridge, upstream, body):
    assert status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, body))) in (400, 413)
    assert not upstream.requests


def test_a_body_must_be_json_and_no_larger_than_the_window_times_four_bytes(bridge, upstream):
    assert status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, BODY, "Content-Type: text/plain", auth=True).replace(
        b"Content-Type: application/json\r\n", b"", 1))) == 400
    over = json.dumps({"model": "", "text": "x" * (4096 * 4)}).encode()
    assert len(over) > 4096 * 4 and status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, over))) == 413
    exact = json.dumps({"model": "", "text": "é" * 2000}).encode()
    assert len(exact) <= 4096 * 4 and status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, exact))) == 200
    assert status_of(talk(bridge.port, asked("GET", "/v1/models", bridge.token, b"{}"))) == 400, "a route that takes no body takes none"
    assert len(upstream.requests) == 1


def test_a_body_that_stalls_ends_the_request_and_never_reaches_the_server(bridge, upstream):
    started = time.monotonic()
    head = asked("POST", "/v1/messages", bridge.token, BODY).partition(b"\r\n\r\n")[0] + b"\r\n\r\n" + BODY[:5]
    answer = talk(bridge.port, head, patience=10)
    assert status_of(answer) == 408 and time.monotonic() - started < 5
    assert not upstream.requests


def tool_named(kind: object) -> dict:
    """A tool as a harness lists it: an input schema with one property called `type` (the `workspace_send` verb has one), whose schema is a dict."""
    return {"name": "workspace_send", "description": "send", "input_schema": {"type": "object", "required": ["type"], "properties": {"type": kind}}}


@pytest.mark.parametrize("kind", [{"type": "string", "enum": ["status", "question"]}, {"type": ["string", "null"]}, {"type": {"type": "string"}}, {"type": None},
                                  {"type": 7}, {"type": True}, {"enum": [{"type": "x"}]}])
def test_a_tool_whose_schema_has_a_property_named_type_is_served_and_the_connection_is_never_dropped(bridge, upstream, kind):
    """The 2026-11-25 live failure: a dict (or a list) where a content part's `type` is read raised `TypeError` in the connection's thread, nothing was
    answered, and the harness saw `ECONNRESET` on each of its eleven attempts."""
    body = json.dumps({"model": ALIAS, "messages": [{"role": "user", "content": "hello"}], "tools": [tool_named(kind)], "max_tokens": 8}).encode()
    answer = talk(bridge.port, asked("POST", "/v1/messages", bridge.token, body))
    assert status_of(answer) == 200, answer[:200]
    assert upstream.body()["tools"] == [tool_named(kind)], "the tool reaches the server as the session wrote it"


def test_a_picture_named_by_an_address_is_still_refused_beside_a_tool_with_a_type_property(bridge, upstream):
    part = {"type": "image", "source": {"type": "url", "url": "http://127.0.0.1:9/x.png"}}
    body = json.dumps({"model": ALIAS, "messages": [{"role": "user", "content": [part]}], "tools": [tool_named({"type": "string"})]}).encode()
    assert status_of(talk(bridge.port, asked("POST", "/v1/messages", bridge.token, body))) == 400
    assert not upstream.requests


def test_a_fault_in_reading_a_request_is_answered_500_and_never_ends_in_a_reset(bridge, upstream, monkeypatch):
    def broken(body: bytes, alias: str, route: str) -> bytes:
        raise TypeError("injected")

    monkeypatch.setattr(model_bridge, "pin", broken)
    answer = talk(bridge.port, asked("POST", "/v1/messages", bridge.token, BODY))
    assert status_of(answer) == 500 and b"injected" not in answer, "the sentence is the bridge's own"
    assert not upstream.requests
    assert bridge.summary()["statuses"] == {"500": 1}


def test_an_upstream_that_is_silent_for_longer_than_the_idle_limit_is_waited_for_and_the_client_is_served():
    """A cold prompt is read by the model for minutes before its first byte: the model thinking is not the client being idle. The upstream here is a
    plumbing server that is NOT a model; it answers 2.5 s after the request, the bridge's idle limit is 1 s, and the body is a stage-sized one."""
    hold = threading.Event()
    server = Upstream(hold)
    made = ModelBridge(server.base_url, Lease(ALIAS, 100000, 1), key=KEY, idle=1.0)
    made.start()
    releaser = threading.Timer(2.5, hold.set)
    try:
        body = json.dumps({"model": ALIAS, "messages": [{"role": "user", "content": "word " * 30000}]}).encode()
        assert len(body) > 150000
        releaser.start()
        started = time.monotonic()
        answer = talk(made.port, asked("POST", "/v1/messages", made.token, body), patience=20)
        assert status_of(answer) == 200 and CANNED_MARKER.encode() in answer, answer[:200]
        assert time.monotonic() - started >= 2.4, "the answer came after the upstream's silence, which was longer than the idle limit"
    finally:
        releaser.cancel()
        hold.set()
        made.stop()
        server.close()


def test_a_client_that_sends_nothing_is_cut_at_the_idle_limit_and_never_reaches_the_server(bridge, upstream):
    started = time.monotonic()
    with socket.create_connection(("127.0.0.1", bridge.port), timeout=10) as sock:
        got = b""
        with contextlib.suppress(TimeoutError, ConnectionResetError):
            while more := sock.recv(65536):
                got += more
    assert time.monotonic() - started < 5, "the idle limit (1 s here) ended it"
    assert status_of(got) == 408, got[:100]
    assert not upstream.requests


def test_a_streamed_answer_passes_as_it_comes_and_not_when_it_is_over():
    server = CannedServer(Canned(pieces=("one", "two", "three"), gap=0.5))
    made = ModelBridge(server.base_url, LEASE, idle=5.0)
    made.start()
    try:
        body = json.dumps({"model": "", "messages": [], "stream": True}).encode()
        with socket.create_connection(("127.0.0.1", made.port), timeout=10) as sock:
            sock.sendall(asked("POST", "/v1/chat/completions", made.token, body))
            seen: list[float] = []
            got = b""
            started = time.monotonic()
            while more := sock.recv(65536):
                got += more
                seen.append(time.monotonic() - started)
        assert b"data: [DONE]" in got and CANNED_MARKER.encode() in got
        assert seen[-1] - seen[0] >= 0.5, f"the first bytes came {seen[-1] - seen[0]:.2f} s before the last: they were not held back"
    finally:
        made.stop()
        server.close()


def scripted(answer: bytes) -> tuple[socket.socket, int]:
    """A listener that reads one request and writes ``answer`` raw: a stand-in for a server that lies about its framing."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(2)

    def serve() -> None:
        connection, _ = sock.accept()
        with connection:
            connection.settimeout(3)
            with contextlib.suppress(OSError):
                connection.recv(65536)
                connection.sendall(answer)
    threading.Thread(target=serve, daemon=True).start()
    return sock, sock.getsockname()[1]


@pytest.mark.parametrize("answer,wanted,expected", [
    (b"not http at all\r\n\r\n", 502, None), (b"", 502, None), (b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", 502, None),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nTransfer-Encoding: chunked\r\n\r\nhello", 502, None),
    (b"HTTP/1.1 200 OK\r\nContent-Length: abc\r\n\r\nhello", 502, None), (b"HTTP/1.1 200 OK\r\n" + b"X-Fill: a\r\n" * 9000, 502, None),
    (b"HTTP/1.1 999 Nope\r\nContent-Length: 2\r\n\r\nok", 502, None),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\nSet-Cookie: a=b\r\nLocation: http://evil.example/\r\n\r\nabcdefghij", 200, b"abc"),
    (b"HTTP/1.1 503 Busy\r\nContent-Length: 4\r\nContent-Type: text/plain\r\n\r\nbusy", 503, b"busy")])
def test_a_hostile_upstream_answer_is_framed_by_the_bridge_or_refused_and_never_passed_as_it_came(answer, wanted, expected):
    listener, port = scripted(answer)
    made = ModelBridge(f"http://127.0.0.1:{port}", LEASE, idle=2.0)
    made.start()
    try:
        got = talk(made.port, asked("POST", "/v1/messages", made.token, BODY), patience=5)
        assert status_of(got) == wanted, got[:200]
        head, _, body = got.partition(b"\r\n\r\n")
        assert b"set-cookie" not in head.lower() and b"location" not in head.lower() and head.lower().count(b"connection: close") == 1
        if expected is not None:
            assert body == expected, "the body is the length the server stated and no more"
    finally:
        made.stop()
        listener.close()


def test_the_default_dial_connects_to_the_address_it_is_given_and_to_nothing_else():
    sink = Sink()
    try:
        assert Wiring().dial is egress_proxy._dial
        egress_proxy._dial("127.0.0.1", sink.port, 2.0).close()
        time.sleep(0.2)
        assert sink.accepted == 1
        spare = socket.socket()
        spare.bind(("127.0.0.1", 0))
        free = spare.getsockname()[1]
        spare.close()
        with pytest.raises(OSError):
            egress_proxy._dial("127.0.0.1", free, 1.0)
    finally:
        sink.close()


def test_an_upstream_that_is_not_a_numeric_loopback_address_is_refused_with_the_sentence():
    for url in ("http://example.com:8080", "http://localhost:8080", "http://192.168.1.5:8080", "http://10.0.0.1:80", "http://[2001:db8::1]:80"):
        with pytest.raises(IsolationRefused, match="only use a model server on this computer"):
            ModelBridge(url, LEASE)
    ModelBridge("http://[::1]:8080", LEASE)


def test_the_token_is_32_random_bytes_held_by_the_bridge_and_never_printed(upstream):
    first, second = (ModelBridge(upstream.base_url, Lease(ALIAS, 1, 1), key=KEY) for _ in range(2))
    assert first.token != second.token and len(first.token) == 43
    for held in (first, second):
        assert held.token not in repr(held) and KEY not in repr(held) and KEY not in str(held.summary())


def test_what_is_kept_is_counts_and_never_a_body(bridge, upstream):
    secret = "the-secret-in-a-body-nobody-keeps"
    talk(bridge.port, asked("POST", "/v1/messages", bridge.token, json.dumps({"model": "", "text": secret}).encode()))
    talk(bridge.port, asked("GET", "/props", bridge.token))
    held = bridge.summary()
    assert held["requests"] == {"/v1/messages": 1, "refused": 1} and held["statuses"] == {"200": 1, "404": 1}
    assert secret not in json.dumps(held) and all(secret not in json.dumps(fields) for _, fields in bridge.heard)


def _confined(code: str, tmp_path: Path, *ports: int, **env: str):
    held = policy(read=[SOURCE, tmp_path], python=True, net=Net.only(*ports) if ports else Net.deny(),
                  env={"HOME": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1", **env})
    return run([os.path.realpath(sys.executable), "-I", "-c", f"import sys; sys.path.insert(0, {SOURCE!r})\n" + code], held, diagnose="never")


def test_a_confined_process_reaches_the_bridge_and_the_servers_own_port_is_in_no_profile(bridge, upstream, tmp_path):
    code = f"""
import http.client, json, socket
def ask(port, method, path, **headers):
    c = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    c.request(method, path, body=json.dumps({{"model": "", "messages": []}}) if method == "POST" else None,
              headers={{"Content-Type": "application/json", **headers}})
    return c.getresponse().status
out = [ask({bridge.port}, "POST", "/v1/messages", Authorization="Bearer {bridge.token}"), ask({bridge.port}, "GET", "/props"),
       ask({bridge.port}, "GET", "/health")]
try:
    out.append(ask({upstream.port}, "POST", "/v1/messages", Authorization="Bearer {KEY}"))
except OSError as e:
    out.append("denied")
print(json.dumps(out))
"""
    done = _confined(code, tmp_path, bridge.port)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout.strip().splitlines()[-1]) == [200, 404, 200, "denied"]
    assert len(upstream.requests) == 1, "the server heard the one request the bridge forwarded and nothing from the session's own attempt"


def test_net_hosts_come_only_from_the_registry_list_and_every_entry_names_its_source_and_its_measured_bound():
    listed = egress.hosts()
    assert {"pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "index.crates.io", "static.crates.io"} <= set(listed)
    for each in listed.values():
        assert each.source.startswith("https://") and each.ecosystem and each.command and each.date
        assert each.bytes_out > 0 and egress.valid_name(each.name)
    assert EgressProxy().registry == listed, "the proxy reads the registry's own list"
    assert EgressProxy(["pypi.org"]).allowed == {"pypi.org"}
    for off in ("evil.example", "github.com", "*.pypi.org", "93.184.216.34", "PYPI.ORG", "pypi.org:443"):
        with pytest.raises(ValueError, match="not on the registry list"):
            EgressProxy(["pypi.org", off])


class Sink:
    """A listener of this test that stands for a registry host's server (and for an internal service that must never be reached): it counts
    the connections it takes and the bytes it is sent, and answers every connection with ``download`` bytes."""

    def __init__(self, download: int = 0) -> None:
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.download, self.accepted, self.received = download, 0, 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                connection, _ = self.sock.accept()
            except OSError:
                return
            self.accepted += 1
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection: socket.socket) -> None:
        with connection:
            connection.settimeout(3)
            try:
                if self.download:
                    connection.sendall(b"d" * self.download)
                while data := connection.recv(65536):
                    self.received += len(data)
            except OSError:
                return

    def close(self) -> None:
        self.sock.close()


PUBLIC = "93.184.216.34"


def names(answer_for: dict[str, list[str]]):
    """A resolver that answers from a table and counts how often it was asked, and the dial that takes the address it is given to ``sink``."""
    asked_of: list[str] = []

    def resolve(host: str, port: int, **_kw: object):
        asked_of.append(host)
        return [(socket.AF_INET6 if ":" in each else socket.AF_INET, socket.SOCK_STREAM, 6, "", (each, port)) for each in answer_for[host]]
    return resolve, asked_of


def proxied(sink: Sink | None, table: dict[str, list[str]], allowed: tuple[str, ...] = ("pypi.org",), **kw):
    """A proxy over the one-host registry of the kit whose resolver says ``table`` and whose dial connects to ``sink``."""
    resolve, asked_of = names(table)
    dialled: list[tuple[str, int]] = []

    def dial(address: str, port: int, timeout: float) -> socket.socket:
        dialled.append((address, port))
        assert sink is not None, "nothing may be dialled"
        return socket.create_connection(("127.0.0.1", sink.port), timeout=timeout)
    made = EgressProxy(allowed, registry=REGISTRY, wiring=Wiring(resolve, dial), idle=2.0, **kw)
    made.start()
    TOKENS[made.port] = made.token
    made.resolved, made.dialled = asked_of, dialled
    return made


TOKENS: dict[int, str] = {}


def basic(token: str) -> str:
    return "Basic " + base64.b64encode(f"poolhouse:{token}".encode()).decode()


def opening(made, target: str = "pypi.org:443") -> bytes:
    """The CONNECT a session's client writes: with the credential the launcher put in its `HTTPS_PROXY`."""
    return f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\nProxy-Authorization: {basic(made.token)}\r\n\r\n".encode()


def connect(port: int, target: str, *, version: str = "1.1", extra: str = "", auth: bool = True) -> bytes:
    held = f"Proxy-Authorization: {basic(TOKENS[port])}\r\n" if auth else ""
    return talk(port, f"CONNECT {target} HTTP/{version}\r\nHost: {target}\r\n{held}{extra}\r\n".encode())


@pytest.mark.parametrize("target", ["pypi.org:80", "pypi.org:8443", "pypi.org:22", "pypi.org:0443", "pypi.org", "pypi.org:", "93.184.216.34:443",
                                    "127.0.0.1:443", "[::1]:443", "*.pypi.org:443", "*:443", "pypi.org.:443", "user@example.com:443",
                                    "pypi.org:443/x", "evil.example:443", "files.pythonhosted.org:443", "localhost:443", "a b:443", "",
                                    "pypi.org:99999999999", "pypi.org%00.evil:443"])
def test_the_egress_proxy_refuses_other_ports_hosts_wildcards_and_literals(target):
    sink = Sink()
    made = proxied(sink, {"pypi.org": [PUBLIC], "files.pythonhosted.org": [PUBLIC], "evil.example": [PUBLIC], "localhost": ["127.0.0.1"]})
    try:
        answer = connect(made.port, target)
        assert status_of(answer) in (400, 403), (target, answer[:120])
        assert b"200" not in answer.split(b"\r\n")[0]
        assert not made.dialled and sink.accepted == 0, "nothing was connected to"
    finally:
        made.stop()
        sink.close()


def test_the_egress_proxy_serves_connect_only_and_never_plain_http_so_there_is_no_redirect_to_follow():
    sink = Sink()
    made = proxied(sink, {"pypi.org": [PUBLIC]})
    try:
        for line in ("GET http://pypi.org/ HTTP/1.1", "GET / HTTP/1.1", "POST http://pypi.org/ HTTP/1.1", "OPTIONS * HTTP/1.1", "HEAD http://pypi.org/ HTTP/1.1"):
            assert status_of(talk(made.port, f"{line}\r\nHost: pypi.org\r\nProxy-Authorization: {basic(made.token)}\r\n\r\n".encode())) in (400, 405), line
        assert not made.dialled and sink.accepted == 0
    finally:
        made.stop()
        sink.close()


@pytest.mark.parametrize("address", ["127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1", "169.254.169.254", "169.254.0.1", "10.0.0.1", "172.16.5.5",
                                     "192.168.1.1", "100.64.0.1", "192.0.2.1", "198.51.100.1", "203.0.113.1", "198.18.0.1", "224.0.0.1",
                                     "239.255.255.250", ".".join(["0"] * 4), "255.255.255.255", "240.0.0.1", "fe80::1", "fc00::1", "fd12::1", "ff02::1",
                                     "2002:7f00:1::", "2002:0a00:0001::", "64:ff9b::7f00:1", "64:ff9b::a9fe:a9fe", "::", "2001:db8::1",
                                     "::ffff:10.0.0.1", "::ffff:169.254.169.254"])
def test_the_egress_proxy_refuses_a_name_that_resolves_into_a_range_it_may_never_reach(address):
    sink = Sink()
    made = proxied(sink, {"pypi.org": [address]})
    try:
        assert status_of(connect(made.port, "pypi.org:443")) == 403
        assert not made.dialled and sink.accepted == 0, "the internal service was never connected to"
        assert blocked(address)
        assert made.counts()["pypi.org"]["refused"] == 1
    finally:
        made.stop()
        sink.close()


def test_a_name_with_one_address_in_a_forbidden_range_among_public_ones_is_refused_whole():
    sink = Sink()
    made = proxied(sink, {"pypi.org": [PUBLIC, "10.0.0.1"]})
    try:
        assert status_of(connect(made.port, "pypi.org:443")) == 403 and not made.dialled and sink.accepted == 0
    finally:
        made.stop()
        sink.close()


def test_a_name_is_resolved_once_and_the_address_that_was_checked_is_the_address_dialled():
    sink = Sink(download=10)
    table: dict[str, list[str]] = {"pypi.org": [PUBLIC]}
    made = proxied(sink, table)
    try:
        assert connect(made.port, "pypi.org:443").startswith(b"HTTP/1.1 200")
        table["pypi.org"] = ["127.0.0.1"]
        assert made.resolved == ["pypi.org"], "one lookup for one connection"
        assert made.dialled == [(PUBLIC, 443)], "the address is the one that was checked, not a name to be looked up again"
        assert status_of(connect(made.port, "pypi.org:443")) == 403, "the next lookup, now private, is refused"
    finally:
        made.stop()
        sink.close()


def test_a_connect_tunnel_is_counted_per_direction_and_cut_when_bytes_out_pass_the_registry_bound_while_a_download_is_not():
    bound = REGISTRY["pypi.org"].bytes_out
    big = Sink(download=40 * bound)
    made = proxied(big, {"pypi.org": [PUBLIC]})
    try:
        with socket.create_connection(("127.0.0.1", made.port), timeout=10) as sock:
            sock.sendall(opening(made))
            got = b""
            while b"\r\n\r\n" not in got:
                got += sock.recv(4096)
            sock.sendall(b"x" * 100)
            total = len(got.partition(b"\r\n\r\n")[2])
            while total < 40 * bound:
                total += len(sock.recv(1 << 20))
        time.sleep(0.2)
        held = made.counts()["pypi.org"]
        assert held["in"] >= 40 * bound and held["out"] == 100 and held["cut"] == 0, f"a download costs nothing against the bound: {held}"
    finally:
        made.stop()
        big.close()
    sink = Sink()
    made = proxied(sink, {"pypi.org": [PUBLIC]})
    cuts: list[tuple[str, dict]] = []
    made.events = lambda name, fields: cuts.append((name, fields))
    try:
        with socket.create_connection(("127.0.0.1", made.port), timeout=10) as sock:
            sock.sendall(opening(made))
            assert sock.recv(4096).startswith(b"HTTP/1.1 200")
            stage = b"s" * (30 * bound)
            try:
                sock.sendall(stage)
                sock.settimeout(5)
                ended = sock.recv(4096) == b""
            except (BrokenPipeError, ConnectionResetError):
                ended = True
            assert ended, "the tunnel is closed on the side that sent too much"
        time.sleep(0.3)
        held = made.counts()["pypi.org"]
        assert held["out"] == bound and held["cut"] == 1, held
        assert sink.received <= bound, f"the host was sent {sink.received} bytes of a {30 * bound} byte upload"
        assert any(name == "isolation.egress_cut" for name, _ in cuts)
        assert status_of(connect(made.port, "pypi.org:443")) == 200, "the bound is one tunnel's: a second normal install is not cut"
        assert sink.accepted == 2
    finally:
        made.stop()
        sink.close()


def test_many_small_tunnels_do_not_get_round_the_budget_of_the_session():
    bound = REGISTRY["pypi.org"].session_bytes
    sink = Sink()
    made = proxied(sink, {"pypi.org": [PUBLIC]})
    try:
        for _ in range(8):
            with socket.create_connection(("127.0.0.1", made.port), timeout=5) as sock:
                sock.sendall(opening(made))
                if not sock.recv(4096).startswith(b"HTTP/1.1 200"):
                    break
                sock.sendall(b"y" * (bound // 5))
                time.sleep(0.15)
        time.sleep(0.3)
        assert made.counts()["pypi.org"]["out"] <= bound and sink.received <= bound
    finally:
        made.stop()
        sink.close()


def test_a_confined_process_can_use_the_proxy_for_a_granted_host_only_and_reaches_nothing_else(tmp_path):
    sink = Sink()
    made = proxied(sink, {"pypi.org": [PUBLIC]})
    try:
        code = f"""
import json, socket
def tunnel(target):
    s = socket.create_connection(('127.0.0.1', {made.port}), timeout=5)
    s.sendall(('CONNECT %s HTTP/1.1\\r\\nHost: %s\\r\\nProxy-Authorization: {basic(made.token)}\\r\\n\\r\\n' % (target, target)).encode())
    return s.recv(4096).split(b'\\r\\n')[0].decode()
out = [tunnel('pypi.org:443'), tunnel('evil.example:443'), tunnel('pypi.org:80')]
try:
    socket.create_connection(('127.0.0.1', {sink.port}), timeout=3)
    out.append('reached the sink')
except OSError:
    out.append('denied')
print(json.dumps(out))
"""
        done = _confined(code, tmp_path, made.port)
        assert done.returncode == 0, done.stderr
        got = json.loads(done.stdout.strip().splitlines()[-1])
        assert got[0].startswith("HTTP/1.1 200") and got[1].startswith("HTTP/1.1 403") and got[2].startswith("HTTP/1.1 403") and got[3] == "denied"
        assert sink.accepted == 1, "only the granted host's tunnel reached the server's stand-in"
    finally:
        made.stop()
        sink.close()


def test_the_reader_is_the_only_parser_and_what_it_accepts_it_writes_itself():
    head = wire.parse_head(b"POST /v1/messages?x=1 HTTP/1.1\r\nHost: a\r\nContent-Length: 2\r\nX-One:  b ")
    assert head.method == "POST" and head.one("x-one") == "b" and head.one("content-length") == "2" and head.one("missing") is None
    assert wire.origin_form("/v1/messages?x=1") == ("/v1/messages", "x=1")
    tree = ast.parse(Path(model_bridge.__file__).read_text())
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert "http.server" not in imported and "socketserver" not in imported, "no second parser sits behind the strict reader"
    assert dataclasses.is_dataclass(wire.Head)

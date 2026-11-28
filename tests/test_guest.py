"""Guest access: joining, the one listener a guest reaches, the guest's local address, and that content is written nowhere.

The model side here is a plumbing server whose every reply is marked canned: ports, TLS, refusals and what reaches disk are under
test, not a model. `tests/test_guest_real.py` runs the real one.
"""

from __future__ import annotations

import argparse
import contextlib
import http.client
import ipaddress
import json
import logging
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from poolhouse import harnessing, home
from poolhouse.guest import local, wire
from poolhouse.guest.control import reach
from poolhouse.guest.gateway import Gateway
from poolhouse.guest.joining import CODE_MOST, JoinError, Joins
from poolhouse.guest.requests import clean
from poolhouse.guest.store import Limits, Store, is_guest_endpoint
from poolhouse.testing.canned import (
    CANNED_HEADER,
    CANNED_HEADER_VALUE,
    CANNED_MARKER,
    canned_chat_reply,
)

MARKER = "zebra-quartz-7719-private-prompt"


class Plumbing(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, size: int = 0) -> None:
        self.seen: list[tuple[str, dict, str]] = []
        self.hold = threading.Event()
        self.hold.set()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.seen.append((self.path, body, self.headers.get("Authorization", "")))
                server.hold.wait(20)
                status, reply = canned_chat_reply(MARKER + "." * size, top={"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header(CANNED_HEADER, CANNED_HEADER_VALUE)
                self.send_header("Set-Cookie", "theirs=1")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

        self.handle_error = lambda *_: None   # this plumbing's own broken pipes, never the gateway's output
        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


class Engine:
    """Hands out the plumbing server's address."""

    def __init__(self, url: str) -> None:
        self.url, self.released = url, []

    def acquire(self, guest):
        return self.url, "server-key"

    def release(self, guest):
        self.released.append(guest)

    def sweep(self, allowed):
        pass

    def close(self):
        pass


@pytest.fixture
def stores(tmp_path):
    return Store(tmp_path / "host-computer"), Store(tmp_path / "guest-computer")


@pytest.fixture
def rig(stores):
    """A joined host and guest: the gateway in front of a plumbing server, and the guest's local address."""
    host, guest = stores
    upstream = Plumbing()
    gateway = Gateway(Engine(f"http://127.0.0.1:{upstream.server_port}"), store=host, port=0)
    gateway.start()
    friend = host.add("guest", "friend", guest.identity().der)
    there = guest.add("host", "them", host.identity().der, (f"127.0.0.1:{gateway.port}",))
    proxy = local.Proxy(there.id, store=guest)
    proxy.start()
    made = argparse.Namespace(host=host, guest=guest, gateway=gateway, upstream=upstream, proxy=proxy, friend=friend, there=there)
    yield made
    upstream.hold.set()
    proxy.close()
    gateway.close()
    upstream.shutdown()
    upstream.server_close()


def ask(rig, body, path="/v1/chat/completions", method="POST", headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", rig.proxy.port, timeout=30)
    sent = body if isinstance(body, bytes) else json.dumps(body).encode()
    connection.request(method, path, sent if method == "POST" else None,
                       {"Authorization": f"Bearer {rig.proxy.token}", **(headers or {})})
    response = connection.getresponse()
    out = response.status, response.read(), dict(response.getheaders())
    connection.close()
    return out


def said(content=MARKER, **more):
    return {"model": "m", "messages": [{"role": "user", "content": content}], **more}


# -- joining --

def joined(stores, shower_role):
    host, guest = stores
    shower, scanner = (host, guest) if shower_role == "host" else (guest, host)
    first, second = Joins(shower), Joins(scanner)
    at = ("127.0.0.1:7453",)
    shown = first.show(shower_role, "shower", listen="127.0.0.1", at=at if shower_role == "host" else ())
    scanned = second.scan(shown["code"], "scanner", at=at if shower_role == "guest" else ())
    return first, second, shown, scanned


@pytest.mark.parametrize("shower_role", ["host", "guest"])
def test_one_scan_binds_both_computers_only_after_both_confirm(stores, shower_role):
    host, guest = stores
    first, second, shown, scanned = joined(stores, shower_role)
    assert len(shown["code"]) <= CODE_MOST and shown["code"].startswith("poolhouse://guest?")
    seen = first.status(shown["id"])
    assert seen["state"] == scanned["state"] == "confirm"
    assert seen["words"] == scanned["words"] and len(seen["words"]) == 4
    assert host.records() == [] and guest.records() == [], "nothing is allowed before the words are confirmed"
    assert first.confirm(shown["id"], True)["state"] == "done"
    assert second.confirm(scanned["id"], True)["state"] == "done"
    assert second.confirm(scanned["id"], True)["state"] == "done"
    [their_guest], [their_host] = host.records(), guest.records()
    assert their_guest.role == "guest" and their_guest.der == guest.identity().der and their_guest.active()
    assert their_host.role == "host" and their_host.der == host.identity().der and their_host.at == ("127.0.0.1:7453",)
    first.close()


def test_words_that_do_not_match_allow_nothing(stores):
    first, second, shown, scanned = joined(stores, "host")
    assert first.confirm(shown["id"], False)["state"] == "refused"
    assert second.confirm(scanned["id"], False)["state"] == "refused"
    assert stores[0].records() == [] and stores[1].records() == []


def test_a_code_is_good_for_one_computer_and_unknown_ids_are_expired(stores, tmp_path):
    first, _second, shown, _scanned = joined(stores, "host")
    late = Joins(Store(tmp_path / "photographer"))
    with pytest.raises(JoinError):
        late.scan(shown["code"], "late")
    assert first.status("no-such-id")["state"] == "expired"
    first.close()


def test_a_wrong_secret_or_key_pins_nothing(stores):
    host, guest = stores
    first = Joins(host)
    shown = first.show("host", "h", listen="127.0.0.1")
    wrong_secret = shown["code"].replace("&s=", "&s=AAAA", 1)
    wrong_secret = wrong_secret[: wrong_secret.index("&e=") - 4] + wrong_secret[wrong_secret.index("&e="):]
    with pytest.raises(JoinError):
        Joins(guest).scan(wrong_secret, "g")
    start = shown["code"].index("&f=") + 3
    wrong_key = shown["code"][:start] + "A" * 43 + shown["code"][start + 43:]
    with pytest.raises(JoinError, match="not the one"):
        Joins(guest).scan(wrong_key, "g")
    assert first.status(shown["id"])["state"] == "waiting" and host.records() == []
    first.close()


def test_only_private_addresses_are_listened_on_or_offered():
    for bad in (str(ipaddress.IPv4Address(0)), "::", "8.8.8.8", "example.com"):
        with pytest.raises(ValueError):
            wire.private_address(bad)
    assert wire.private_address("100.101.102.103") == "100.101.102.103"
    found = argparse.Namespace(up=True, addresses=("100.101.102.103", "8.8.4.4"))
    assert reach(found)[0] == "100.101.102.103" and "8.8.4.4" not in reach(found)


# -- the listener a guest reaches --

def test_a_joined_guest_gets_an_answer_and_only_counts_are_kept(rig):
    status, body, headers = ask(rig, said(id_slot=3, cache_prompt=False, lora=[{}], n=4, max_tokens=10**9))
    assert status == 200 and CANNED_MARKER.encode() in body and "Set-Cookie" not in headers
    [(path, sent, key)] = rig.upstream.seen
    assert path == "/v1/chat/completions" and key == "Bearer server-key"
    assert not {"id_slot", "cache_prompt", "lora", "n"} & set(sent)
    assert sent["max_tokens"] == Limits().output_tokens_per_request
    deadline = time.time() + 5
    while not rig.host.counts(rig.friend.id)["requests"] and time.time() < deadline:
        time.sleep(0.02)
    counts = rig.host.counts(rig.friend.id)
    assert (counts["requests"], counts["input_tokens"], counts["output_tokens"]) == (1, 7, 3)


@pytest.mark.parametrize("path", ["/slots", "/slots/0?action=save", "/props", "/metrics", "/ui", "/ui/chat", "/jobs", "/files/x",
                                  "/infer/slots", "/v1/../slots", "/completion", "/lora-adapters"])
def test_a_guest_reaches_nothing_but_model_calls(rig, path):
    status, _body, _headers = ask(rig, said(), path=path)
    direct, response = local.connect(rig.guest, rig.there, path, b"{}")
    assert status == 404 and response.status == 404 and rig.upstream.seen == []
    response.read()
    direct.close()


def test_a_computer_that_was_not_joined_gets_no_connection(rig, tmp_path):
    stranger = Store(tmp_path / "stranger")
    known = stranger.add("host", "them", rig.host.identity().der, (f"127.0.0.1:{rig.gateway.port}",))
    with pytest.raises(OSError):
        local.connect(stranger, known, "/v1/models", timeout=10)
    bare = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    bare.check_hostname, bare.verify_mode = False, ssl.CERT_NONE
    connection = http.client.HTTPSConnection("127.0.0.1", rig.gateway.port, timeout=10, context=bare)
    with pytest.raises((OSError, http.client.HTTPException)):
        connection.request("GET", "/v1/models")
        connection.getresponse()


def test_the_guest_accepts_only_the_joined_certificate(rig, tmp_path):
    other = Store(tmp_path / "impostor")
    wrong = rig.guest.add("host", "impostor", other.identity().der, (f"127.0.0.1:{rig.gateway.port}",))
    with pytest.raises(OSError):
        local.connect(rig.guest, wrong, "/v1/models", timeout=10)


def test_removal_cuts_a_request_in_flight_and_every_later_one(rig):
    rig.upstream.hold.clear()
    got = {}
    asking = threading.Thread(target=lambda: got.update(answer=ask(rig, said())), daemon=True)
    asking.start()
    deadline = time.time() + 10
    while not rig.upstream.seen and time.time() < deadline:
        time.sleep(0.02)
    rig.host.revoke(rig.friend.id)
    asking.join(8)
    assert not asking.is_alive(), "the request ends while the model still has not answered"
    assert got["answer"][0] != 200 and CANNED_MARKER.encode() not in got["answer"][1]
    rig.upstream.hold.set()
    assert ask(rig, said())[0] == 502


def test_limits_refuse_further_requests(rig):
    rig.host.change(rig.friend.id, limits=Limits(requests_per_minute=1))
    assert ask(rig, said())[0] == 200
    assert ask(rig, said())[0] == 429
    rig.host.change(rig.friend.id, limits=Limits(tokens_per_day=1))
    assert ask(rig, said())[0] == 429 and len(rig.upstream.seen) == 1


@pytest.mark.parametrize("content", [
    [{"type": "image_url", "image_url": {"url": "http://192.168.1.1/admin"}}],
    [{"type": "image", "source": {"type": "url", "url": "https://internal/x"}}],
    [{"type": "input_image", "image_url": "file:///etc/passwd"}],
])
def test_a_guest_cannot_make_the_host_fetch_anything(content):
    with pytest.raises(ValueError, match="link"):
        clean("/v1/chat/completions", json.dumps(said(content)).encode(), 100)


def test_a_data_picture_and_a_tools_own_arguments_pass():
    content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
               {"type": "tool_use", "input": {"url": "https://example.org", "image_url": "x"}}]
    assert json.loads(clean("/v1/messages", json.dumps(said(content)).encode(), 100))["max_tokens"] == 100
    with pytest.raises(ValueError, match="itself"):
        clean("/v1/chat/completions", json.dumps(said(response_format={"schema": {"$ref": "http://x/y"}})).encode(), 100)


def test_the_local_address_refuses_pages_other_names_and_strangers(rig):
    assert ask(rig, said(), headers={"Origin": "http://evil.example"})[0] == 403
    assert ask(rig, said(), headers={"Host": "evil.example"})[0] == 403
    assert ask(rig, said(), headers={"Authorization": "Bearer guess"})[0] == 403
    assert rig.upstream.seen == []
    assert rig.proxy.server.server_address[0] == "127.0.0.1"


def test_a_guest_who_does_not_read_her_reply_does_not_keep_the_owner_waiting(stores):
    host, guest = stores
    upstream = Plumbing(size=32 << 20)
    given_back = threading.Event()

    @contextlib.contextmanager
    def turn(url):
        try:
            yield
        finally:
            given_back.set()

    gateway = Gateway(Engine(f"http://127.0.0.1:{upstream.server_port}"), store=host, port=0, turn=turn)
    gateway.start()
    host.add("guest", "slow", guest.identity().der)
    there = guest.add("host", "them", host.identity().der, (f"127.0.0.1:{gateway.port}",))
    connection, response = local.connect(guest, there, "/v1/chat/completions", json.dumps(said()).encode(), timeout=30)
    try:
        assert response.status == 200
        assert given_back.wait(10), "the device is given back while the reply is still unread"
    finally:
        connection.close()
        gateway.close()
        upstream.shutdown()
        upstream.server_close()


# -- nothing on disk, nothing in a log --

def test_a_marker_prompt_and_reply_reach_no_file_and_no_log(rig, tmp_path, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    assert ask(rig, said())[0] == 200
    assert ask(rig, f'{{"{MARKER}"'.encode())[0] == 400
    assert ask(rig, said([{"type": "image_url", "image_url": {"url": f"http://{MARKER}/"}}]))[0] == 400
    assert ask(rig, said(), path=f"/{MARKER}")[0] == 404
    rig.upstream.shutdown()
    rig.upstream.server_close()
    assert ask(rig, said())[0] == 502
    time.sleep(0.5)
    roots = {tmp_path, home.home(), home.cache()}
    files = [p for root in roots if root.is_dir() for p in root.rglob("*") if p.is_file() and not p.is_symlink()]
    assert any(p.name == "counts.json" for p in files), "the search covers the host's guest state"
    assert [str(p) for p in files if MARKER.encode() in p.read_bytes() or MARKER in p.name] == []
    out = capfd.readouterr()
    assert MARKER not in caplog.text + out.out + out.err


# -- a harness pointed at a guest connection --

def test_a_harness_pointed_at_a_guest_connection_is_sandboxed(monkeypatch, tmp_path, stores):
    host, _ = stores
    mine = Store()
    there = mine.add("host", "them", host.identity().der, ("127.0.0.1:1",))
    proxy = local.Proxy(there.id, store=mine)
    proxy.start()
    monkeypatch.setattr(harnessing, "admit", lambda *a, **k: None)
    monkeypatch.setattr(harnessing, "check_project", lambda project, **k: Path(tmp_path))
    try:
        for url, boxed in ((proxy.url, True), ("http://127.0.0.1:9", False)):
            args = argparse.Namespace(on=url, model="", permissions="", execution_mode="act", project=str(tmp_path), sandbox=False,
                                      read=[], toolchain=[], protect=[])
            monkeypatch.setattr(harnessing.isolation, "require_attended", lambda *_: None)
            monkeypatch.setattr(harnessing.coding_policy, "Policy", lambda *a: None)
            assert harnessing.checked(args, "codex", lambda _: None, interactive=False)
            assert args.sandbox is boxed
    finally:
        proxy.close()
    assert not is_guest_endpoint(proxy.url)
    assert list((mine.root / "endpoints").iterdir()) == []

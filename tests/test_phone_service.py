"""The phone listener over real TLS on loopback: linking by code, signed requests and what they may do (docs/phone-api.md)."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import http.client
import ipaddress
import json
import time

import pytest
from phone_kit import Key

from poolhouse.fleet import tls
from poolhouse.fleet.phone import PhoneService
from poolhouse.fleet.phone.link import ALPHABET, MOST_FAILURES, code_key, message, normalised
from poolhouse.fleet.phone.server import Window, reachable
from poolhouse.fleet.ui import UI
from poolhouse.phone_mailbox import Store


class Actions:
    """Stands in for the tools layer the daemon's command hands in (plumbing only: nothing is started)."""

    def __init__(self):
        self.asked = []

    def board_agents(self, projects):
        return []

    def start_delegate(self, project, task, edit, model):
        self.asked.append(("start", project, task, edit, model))
        return "started nothing (test)"

    def stop_delegate(self, name):
        self.asked.append(("stop", name))
        return "stopped nothing (test)"


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    made = PhoneService(UI(name="Studio", gated=False), Actions(), store=Store(tmp_path / "phones"), port=0,
                        addresses=lambda: ["127.0.0.1"])
    yield made
    made.close()


class Client:
    """The app's side: it trusts the one pinned certificate and signs each request with the phone's key."""

    def __init__(self, service):
        self.service, self.key = service, Key()
        self.pin = service.identity().fingerprint
        self.offset = 0.0

    def raw(self, method, path, body=b"", headers=None, context=None):
        context = context or tls.certificate_context(self.service.identity().beacon)
        link = http.client.HTTPSConnection("127.0.0.1", self.service.listening["port"], context=context, timeout=20)
        try:
            link.request(method, path, body=body or None, headers=headers or {})
            answer = link.getresponse()
            return answer.status, json.loads(answer.read() or b"{}")
        finally:
            link.close()

    def link(self, code, name="Pixel", pin=None):
        pin = pin or self.pin
        said = message("poolhouse/phone-link/v1", pin, self.key.public, name)
        proof = base64.b64encode(hmac.new(code_key(code, pin), said, hashlib.sha256).digest()).decode()
        body = {"name": name, "key": self.key.public, "proof": proof, "signature": self.key.sign(said)}
        status, got = self.raw("POST", "/phone/v1/link", json.dumps(body).encode())
        if status == 200:
            self.key.phone = got["result"]["phone"]
        return status, got

    def headers(self, signed):
        return {"Poolhouse-Phone": signed.phone, "Poolhouse-Time": str(signed.time_ms), "Poolhouse-Nonce": signed.nonce,
                "Poolhouse-Signature": signed.signature}

    def call(self, method, path, body=None, **how):
        signed = self.key.signed(method, path, body, pin=self.pin, **how)
        return self.raw(method, path, signed.body.encode(), self.headers(signed))


@pytest.fixture
def phone(service):
    offer = service.offer()
    client = Client(service)
    status, got = client.link(offer["code"])
    assert status == 200, got
    return client


def test_nothing_listens_until_a_code_is_open_or_a_phone_is_linked(service):
    service.sync()
    assert service.listening == {"addresses": [], "port": 0}
    service.offer()
    assert service.listening["addresses"] == ["127.0.0.1"]
    service.cancel_offer()
    assert service.listening == {"addresses": [], "port": 0}


def test_a_typed_code_links_one_phone_and_the_computer_proves_it_knows_the_code(service):
    offer = service.offer()
    code = normalised(offer["code"])
    assert len(code) == 10 and set(code) <= set(ALPHABET)
    assert offer["link"].startswith(f"poolhouse://link?v=1&pin={service.identity().fingerprint}&code={code}&at=127.0.0.1%3A")
    assert offer["qr_svg"].startswith("<svg")
    client = Client(service)
    status, hello = client.raw("GET", "/phone/v1/hello")
    assert hello["result"]["linking"] is True and hello["result"]["computer"] == "Studio"
    status, got = client.link(offer["code"].lower())
    assert status == 200
    expected = message("poolhouse/phone-linked/v1", client.pin, client.key.public, client.key.phone)
    assert base64.b64decode(got["result"]["proof"]) == hmac.new(code_key(code, client.pin), expected, hashlib.sha256).digest()
    assert [p.name for p in service.store.phones().values()] == ["Pixel"]
    assert Client(service).link(offer["code"])[0] == 403   # spent


def test_a_wrong_code_or_another_certificate_links_nothing_and_guessing_is_held(service):
    offer = service.offer()
    client = Client(service)
    assert client.link("22222-22222")[0] == 403
    assert client.link(offer["code"], pin="00" * 32)[0] == 403   # a proof made to someone in between
    for _ in range(MOST_FAILURES - 2):
        client.link("33333-33333")
    assert client.link(offer["code"])[0] == 429
    assert service.store.phones() == {}


def test_an_unsigned_request_reads_nothing(service, phone):
    status, got = phone.raw("GET", "/phone/v1/sessions")
    assert (status, got["error"]["code"]) == (401, "unlinked")
    status, got = phone.raw("GET", "/phone/v1/sessions", headers={"Poolhouse-Phone": phone.key.phone})
    assert (status, got["error"]["code"]) == (401, "signature")


def test_only_the_phones_key_over_this_very_request_is_admitted_once(service, phone):
    assert phone.call("GET", "/phone/v1/me")[1]["result"]["name"] == "Pixel"
    stranger = Key()
    stranger.phone = phone.key.phone
    forged = stranger.signed("GET", "/phone/v1/me", pin=phone.pin)
    assert phone.raw("GET", "/phone/v1/me", headers=phone.headers(forged))[0] == 401
    signed = phone.key.signed("GET", "/phone/v1/me", pin=phone.pin)
    assert phone.raw("GET", "/phone/v1/me", headers=phone.headers(signed))[0] == 200
    assert phone.raw("GET", "/phone/v1/me", headers=phone.headers(signed))[0] == 401   # replayed
    assert phone.raw("GET", "/phone/v1/jobs", headers=phone.headers(phone.key.signed("GET", "/phone/v1/me", pin=phone.pin)))[0] == 401
    swapped = phone.key.signed("POST", "/phone/v1/pool-use", {"enabled": False}, pin=phone.pin)
    assert phone.raw("POST", "/phone/v1/pool-use", b'{"enabled": true}', phone.headers(swapped))[0] == 401
    assert service.store.phones()[phone.key.phone].pool_use is False
    assert phone.call("GET", "/phone/v1/me", at=time.time() - 600)[0] == 401
    other = dataclasses.replace(phone.key.signed("GET", "/phone/v1/me", pin="11" * 32))
    assert phone.raw("GET", "/phone/v1/me", headers=phone.headers(other))[0] == 401   # signed for another computer


def test_a_removed_phone_is_refused_at_once(service, phone):
    assert service.remove(phone.key.phone)
    service.offer()   # keeps the listener up to answer
    assert phone.call("GET", "/phone/v1/me")[1]["error"]["code"] == "unlinked"


def test_a_phone_unlinks_itself(service, phone):
    assert phone.call("DELETE", "/phone/v1/me")[1]["result"] == {"removed": True}
    assert service.store.phones() == {}


def test_the_listener_hears_only_this_computer_its_home_network_and_tailscale():
    for near in ("127.0.0.1", "192.168.1.4", "10.1.2.3", "172.20.0.1", "100.101.102.103", "fd7a:115c:a1e0::1", "::1"):
        assert reachable(near) and reachable(near, caller=True), near
    every = str(ipaddress.IPv4Address(0))
    for far in ("8.8.8.8", "172.32.0.1", "100.128.0.1", "2001:db8::1", every, "::", "::ffff:8.8.8.8", "host.example"):
        assert not reachable(far) and not reachable(far, caller=True), far
    assert reachable("169.254.1.1", caller=True) and not reachable("169.254.1.1")


def test_a_rate_window_admits_its_number_and_then_waits():
    at = [0.0]
    window = Window(2, 60, clock=lambda: at[0])
    assert [window.admit("a"), window.admit("a"), window.admit("a"), window.admit("b")] == [True, True, False, True]
    at[0] = 61
    assert window.admit("a")


def test_plain_text_and_large_bodies_are_refused(service, phone):
    link = http.client.HTTPConnection("127.0.0.1", service.listening["port"], timeout=5)
    with pytest.raises((OSError, http.client.HTTPException)):
        link.request("GET", "/phone/v1/hello")
        link.getresponse()
    status, got = phone.call("POST", "/phone/v1/pool-use", {"enabled": True, "pad": "x" * 70_000})
    assert (status, got["error"]["code"]) == (413, "too_large")


def test_devices_list_this_computer_and_the_phone(service, phone):
    got = phone.call("GET", "/phone/v1/devices")[1]["result"]
    kinds = {d["kind"]: d for d in got["devices"]}
    assert kinds["computer"]["this_computer"] and kinds["computer"]["memory"]["total_bytes"] > 0
    assert kinds["phone"]["name"] == "Pixel" and kinds["phone"]["pool_use"] is False


def test_what_a_computer_cannot_do_is_said(service, phone):
    assert phone.call("GET", "/phone/v1/jobs")[0] == 501
    assert phone.call("POST", "/phone/v1/models/start", {"path": "/x"})[0] == 501
    assert phone.call("GET", "/phone/v1/scheduled")[1]["result"] in ({"available": False, "jobs": []}, {"available": True, "jobs": []})
    assert phone.call("GET", "/phone/v1/nothing")[0] == 404


def test_scheduled_jobs_are_the_computers_own_records(service, phone):
    cancelled = []

    class Records:
        """The record module's calls (plumbing only)."""

        import re

        ID = re.compile(r"s-\d+")

        def jobs(self):
            return [{"id": "s-1"}, {"id": "s-2", "refused": "no"}]

        def view(self, row):
            return {"id": row["id"]}

        def load(self, ident):
            return {"id": ident} if ident == "s-1" else None

        def log_tail(self, ident):
            return "ran once"

        def cancel(self, ident, by):
            cancelled.append((ident, by))

    service.schedule = Records()
    assert phone.call("GET", "/phone/v1/scheduled")[1]["result"] == {"available": True, "jobs": [{"id": "s-1"}]}
    assert phone.call("GET", "/phone/v1/scheduled/s-1")[1]["result"]["log"] == "ran once"
    assert phone.call("POST", "/phone/v1/scheduled/s-9/cancel", {})[0] == 404
    assert phone.call("POST", "/phone/v1/scheduled/s-1/cancel", {})[1]["result"] == {"cancelled": True}
    assert cancelled == [("s-1", f"the person, from the linked phone {phone.key.phone}")]


def test_a_delegate_starts_only_on_a_known_project(service, phone, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    assert phone.call("POST", "/phone/v1/delegates", {"project": "feedfacefeed", "task": "t"})[0] == 404
    service.store.project(str(project), True)
    [row] = phone.call("GET", "/phone/v1/projects")[1]["result"]["projects"]
    status, got = phone.call("POST", "/phone/v1/delegates", {"project": row["id"], "task": "read the docs", "edit": True})
    assert status == 201, got
    assert service.actions.asked == [("start", str(project), "read the docs", True, "")]
    assert "delegate-start" in (service.store.root / "audit.jsonl").read_text()


def test_the_stream_carries_a_new_event_to_the_phone_and_ends_on_removal(service, phone):
    ident = "claude-11111111-2222-3333-4444-555555555555"
    service.store.report(ident, connected=True, state="idle")
    signed = phone.key.signed("GET", "/phone/v1/stream", pin=phone.pin)
    link = http.client.HTTPSConnection("127.0.0.1", service.listening["port"],
                                       context=tls.certificate_context(service.identity().beacon), timeout=12)
    try:
        link.request("GET", "/phone/v1/stream", headers=phone.headers(signed))
        answer = link.getresponse()
        assert answer.getheader("Content-Type") == "text/event-stream"
        assert answer.readline() == b": ping\n"
        time.sleep(1.5)
        service.store.event(ident, "notice", kind="permission", text="waits at the computer")
        heard = []
        while not heard or "waits at the computer" not in heard[-1]:
            line = answer.readline()
            assert line, heard
            heard.append(line.decode())
        assert "event: session\n" in heard
        service.remove(phone.key.phone)
        while line := answer.readline():
            pass
    finally:
        link.close()


def test_messages_are_held_to_a_rate(service, phone):
    statuses = [phone.call("POST", "/phone/v1/sessions/claude-none/messages", {"text": "x"})[0] for _ in range(31)]
    assert statuses[:30] == [404] * 30 and statuses[30] == 429

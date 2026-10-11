"""The step about a phone (docs/phone-approval.md): on each device, a phone made of keys is added to the node and approves what a session asks.

The phone is the rig's stand-in for a person's phone and nothing more: a P-256 key made in this run that signs the challenge the node wrote,
and the HTTPS requests a phone makes, which the device itself sends to its own node's phone endpoint over TLS 1.3 pinned to the certificate that
node made. The node cannot tell it from a phone, and nothing in the node knows a rig is running. What the rig does not exercise is the
phone's custody of the key (Android Keystore, the fingerprint prompt) and the push (docs/phone-approval.md, "Checks on a real phone").
"""

from __future__ import annotations

import base64
import json
import threading
import time

from phone_keys import PhoneKeys
from pool_check.api_session import RigSession
from pool_check.device import BOARD, Device
from pool_check.world import World

from poolhouse.board import founding, grants, presence

ASK = {"action": "sync_now", "resource": "*", "lasts_ms": 600_000, "uses": 1}


def _phone(device: Device, op: str, token: str = "", body: dict | None = None, method: str = "POST") -> dict:
    """One request of a phone to ``device``'s own endpoint: the node's envelope, with the HTTP status in ``status``."""
    sent = device.wire("phone", "--state", device.state_text, op, "--token", token, "--body", json.dumps(body or {}), "--method", method)["result"]
    return {**sent["reply"], "status": sent["status"]}


def link_secret(link: str) -> str:
    """The one-time secret a phone's code carries: the link is `poolhouse://add-phone?data=` and the base64url of a JSON object."""
    data = link.split("data=", 1)[1]
    return str(json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))["secret"])


def _live(device: Device, name: str) -> list[str]:
    """The actions the node lists as live for the session ``name`` of the demo board, carried by the node itself (an approval wrote them)."""
    listed = device.call("grants", BOARD, device.token)["grants"]
    return sorted(g["action"] for g in listed if g["status"] == "live" and g["carried_by"] == "node" and g["to"].get("name") == name)


def _linked(world: World, device: Device) -> PhoneKeys:
    """Add a phone made of keys to ``device``: its node listens, a code is made, the phone uses it and the person's key approves it."""
    person = world.person
    device.wire("phone-listen", "--state", device.state_text)
    device.stop_node()
    device.start_node()
    token = device.admin_session()[0]
    assert device.call("phone_info")["listening"], f"{device.name}'s node does not listen for phones after its restart"

    phone = PhoneKeys(f"phone of {device.name}")
    code = link_secret(str(device.call("phone_offer", "", token)["link"]))
    added = _phone(device, "add", body=phone.add_body(str(device.hello()["device"]), code))
    assert added["ok"] and added["result"]["state"] == "pending", f"{device.name}: a phone added after a key exists should wait for approval: {added}"
    phone.token = added["result"]["token"]
    listing = grants.sign_list(person.key, str(device.hello()["device"]), int(time.time() * 1000))
    waiting = next(a for a in device.call("approval_pending", "", token, **listing)["approvals"] if a["id"] == added["result"]["approval"])
    assert "POOL: SECURE." in waiting["text"], f"{device.name}: adding a phone states the pool's policy:\n{waiting['text']}"
    approval = grants.sign_approval(person.key, waiting, "approve")
    assert len(added["result"]["number"]) == 2, f"{device.name}: the new phone shows no number for the approving key to pick"
    approved = device.call("approval_decide", "", token, **approval, pick=added["result"]["number"])
    assert approved["state"] == "approved", f"{device.name}: the person's key did not approve the phone: {approved}"
    assert len(device.call("person_keys")["keys"]) == 2, f"{device.name}: the phone's key is not a person key"
    assert _phone(device, "status", phone.token)["result"]["state"] == "active"
    return phone


def _on(world: World, device: Device) -> str:
    """The whole scenario on one device; what it showed."""
    phone = _linked(world, device)
    asked = device.call("approval_request", BOARD, device.token, asks=[ASK], note="the pool check asks")
    seen = _phone(device, "get", phone.token, {"id": asked["id"]})["result"]
    for label in ("NOTE:", "POOL:", "RISK:", "WHO:", "ASK:", "SCOPE:", "CANNOT:", "WHY:", "DENY:", "EXPIRES:"):
        assert label in seen["text"], f"{device.name}: the text the person reads has no {label} line:\n{seen['text']}"
    assert seen["challenge"]["text"] == seen["text"], "what is signed is what is shown"

    forged = dict(seen, challenge=dict(seen["challenge"], text=seen["text"].replace("once", "a hundred times")))
    refused = _phone(device, "decide", body=phone.decision(forged, "approve"))
    assert not refused["ok"] and "does not match this request" in refused["error"]["message"], f"{device.name}: text the node did not write was signed: {refused}"
    assert _live(device, device.session) == [], f"{device.name}: a refused signature granted something"
    liar = device.try_call("approval_decide", "", device.token, id=asked["id"], decision="approve", key=phone.fingerprint, sig="30" + "00" * 40)
    assert not liar["ok"] and "carry the answer to its own request" in liar["error"], f"{device.name}: the session that asked carried an answer: {liar}"

    done = _phone(device, "decide", body=phone.decision(seen, "approve"))
    assert done["ok"] and done["result"]["state"] == "approved", f"{device.name}: the phone's approval was not taken: {done}"
    again = _phone(device, "decide", body=phone.decision(seen, "approve"))
    assert not again["ok"] and "already approved" in again["error"]["message"], f"{device.name}: a decision was taken twice: {again}"
    assert _live(device, device.session) == ["sync_now"], f"{device.name}: the approved grant is not live for the session"
    assert device.call("approval_status", BOARD, device.token, id=asked["id"])["state"] == "approved"

    second = device.call("approval_request", BOARD, device.token, asks=[ASK])
    shown = _phone(device, "get", phone.token, {"id": second["id"]})["result"]
    assert _phone(device, "decide", body=phone.decision(shown, "deny"))["result"]["state"] == "denied"
    assert device.call("approval_status", BOARD, device.token, id=second["id"])["state"] == "denied"
    stranger = _phone(device, "list", "not-a-token")
    assert stranger["status"] == 401, f"{device.name}: a token the node never gave read the requests: {stranger}"
    return (f"{device.name}: a phone was added (approved by the person's key), a request was approved once and denied once, text the node did not write and a second decision were refused, "
            f"and a token the node never gave read nothing; {_pestered(device, phone)}")


def _pestered(device: Device, phone: PhoneKeys) -> str:
    """A session that asks again and again: the same request is one item, a wrong number is a decline, what was declined waits and is asked on its own, and a block ends it."""
    token, name = device.bare_session(BOARD, "pester")
    ask = {"action": "add_device", "resource": "*", "lasts_ms": 600_000, "uses": 1}
    first = device.call("approval_request", BOARD, token, asks=[ask])
    assert len(first["code"]) == 2 and len(first["challenge"]["context"]["pick"]) == 3 and first["code"] in first["challenge"]["context"]["pick"], f"{device.name}: no number to match: {first}"
    for i in range(20):
        again = device.call("approval_request", BOARD, token, asks=[{**ask, "uses": 1 + i % 3}], note=f"again {i}")
        assert again["id"] == first["id"] and again["deduplicated"], f"{device.name}: asking again made a new request: {again}"
    waiting = [a for a in device.call("approval_pending", "", token)["approvals"] if a["kind"] == "grants" and a["session"] == name]
    assert len(waiting) == 1, f"{device.name}: {len(waiting)} requests wait after twenty-one identical asks"
    seen = _phone(device, "get", phone.token, {"id": first["id"]})["result"]
    wrong = next(n for n in seen["challenge"]["context"]["pick"] if n != first["code"])
    refused = _phone(device, "decide", body={**phone.decision(seen, "approve"), "pick": wrong})
    assert not refused["ok"] and "not the one the agent showed" in refused["error"]["message"], f"{device.name}: a wrong number approved or was not refused: {refused}"
    assert device.call("approval_status", BOARD, token, id=first["id"])["state"] == "denied", f"{device.name}: a wrong number is not a decline"
    soon = device.try_call("approval_request", BOARD, token, asks=[ask])
    assert not soon["ok"] and "declined a moment ago" in soon["error"], f"{device.name}: a declined kind was asked again at once: {soon}"
    batched = device.try_call("approval_request", BOARD, token, asks=[ask, {"action": "link_read", "resource": "a>b", "lasts_ms": 600_000}])
    assert not batched["ok"] and "asked for on its own" in batched["error"], f"{device.name}: a scope that was declined was batched: {batched}"
    blocked = device.call("approval_request", BOARD, token, asks=[{"action": "link_read", "resource": "a>b", "lasts_ms": 600_000}])
    shown = _phone(device, "get", phone.token, {"id": blocked["id"]})["result"]
    assert "If you deny and block, it also cannot ask again for 1 day." in shown["text"], f"{device.name}: the text does not say what a block does"
    assert _phone(device, "decide", body=phone.decision(shown, "block"))["result"]["state"] == "denied"
    after = device.try_call("approval_request", BOARD, token, asks=[{"action": "link_read", "resource": "c>d", "lasts_ms": 600_000}])
    assert not after["ok"] and "A person blocked this agent" in after["error"], f"{device.name}: a blocked session asked again: {after}"
    return "twenty-one identical asks were one request, a wrong number was a decline, what was declined waited and was asked on its own, and a block ended it"


def a_phone_approves_what_a_session_asks_on_every_device(world: World) -> str:
    """On each device a phone made of keys is added (a later phone waits for the person's key to approve it), a session's request reads in full with every part, a signature over other text is refused, the request is approved once and its grant is live, a second one is denied, and nothing a session or a stranger sends decides."""
    world.fresh()
    return "; ".join(_on(world, device) for device in world.devices)


def _waiting(device: Device, phone: PhoneKeys, wait_s: float = 60.0) -> dict:
    """The next request that waits on ``phone``, as the phone reads it."""
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        listed = [r for r in _phone(device, "list", phone.token)["result"]["approvals"] if r["state"] == "pending"]
        if listed:
            return dict(_phone(device, "get", phone.token, {"id": listed[0]["id"]})["result"])
        time.sleep(0.3)
    raise AssertionError(f"{device.name}: the agent never asked the phone")


def a_phone_founds_the_pool_by_signing(world: World) -> str:
    """On the first device a phone made of keys is added and the agent's founding path asks it twice, to accept the charter and to admit this device: the phone signs the bytes the node gave it and the pool is governed by its key."""
    world.fresh()
    device = world.first
    phone = _linked(world, device)
    told: list[str] = []
    done: dict = {}
    session = RigSession(device)
    terms = founding.Terms(voice=presence.Voice(say=told.append), wait_s=90.0, cert_hex=device.certificate())

    def agent() -> None:
        try:
            done["made"] = founding.found_by_phone(session, "phone pool", "secure", terms)
        except founding.Refusal as refused:
            done["refused"] = refused

    thread = threading.Thread(target=agent)
    thread.start()
    card = _waiting(device, phone)
    assert card["challenge"]["action"]["kind"] == "sign" and "Found the pool 'phone pool', secure." in card["text"] and "POOL: SECURE." in card["text"], card["text"]
    swapped = _phone(device, "decide", body=phone.decision(card, "approve"))
    assert not swapped["ok"] and device.pool()["governance"]["mode"] == "unfounded", f"{device.name}: a signature over the challenge founded a pool: {swapped}"
    signed = _phone(device, "decide", body=phone.signature(card))
    assert signed["ok"] and signed["result"]["state"] == "approved", f"{device.name}: the phone's signature was not taken: {signed}"
    admit = _waiting(device, phone)
    assert "ASK: Let the device" in admit["text"] and admit["id"] != card["id"], admit["text"]
    assert _phone(device, "decide", body=phone.signature(admit))["ok"]
    thread.join(60)
    assert "made" in done, f"{device.name}: founding by phone ended with {done.get('refused')}"
    gov = device.pool()["governance"]
    assert (gov["mode"], gov["name"], gov["weak_keys_only"]) == ("governed", "phone pool", False), gov
    assert [(a["strength"], a["fingerprint"]) for a in gov["admins"]] == [("strong", phone.fingerprint)], f"{device.name}: the phone's key is not the pool's one strong admin: {gov['admins']}"
    assert [m["status"] for m in device.members() if m.get("self")] == ["active"], f"{device.name} is not the pool's first member"
    again = founding.found_by_phone(session, "phone pool", "secure", terms)
    assert again.already, "a second call founded another pool"
    assert any("Found the pool" in line for line in told), told
    return f"{device.name}: two cards (accept the charter, admit this device) were signed by the phone's P-256 key, a signature over the challenge was refused, and the pool is governed by that one strong key"

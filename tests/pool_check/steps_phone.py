"""The step about a phone (docs/phone-approval.md): on each device, a phone made of keys is added to the node and approves what a session asks.

The phone is the rig's stand-in for a person's phone and nothing more: a P-256 key made in this run that signs the challenge the node wrote,
and the HTTPS requests a phone makes, which the device itself sends to its own node's phone endpoint over TLS 1.3 pinned to the certificate that
node made. The node cannot tell it from a phone, and nothing in the node knows a rig is running. What the rig does not exercise is the
phone's custody of the key (Android Keystore, the fingerprint prompt) and the push (docs/phone-approval.md, "Checks on a real phone").
"""

from __future__ import annotations

import json

from phone_keys import PhoneKeys
from pool_check.device import BOARD, Device
from pool_check.world import World

from poolhouse.board import grants

ASK = {"action": "sync_now", "resource": "*", "lasts_ms": 600_000, "uses": 1}


def _phone(device: Device, op: str, token: str = "", body: dict | None = None, method: str = "POST") -> dict:
    """One request of a phone to ``device``'s own endpoint: the node's envelope, with the HTTP status in ``status``."""
    sent = device.wire("phone", "--state", device.state_text, op, "--token", token, "--body", json.dumps(body or {}), "--method", method)["result"]
    return {**sent["reply"], "status": sent["status"]}


def _live(device: Device, name: str) -> list[str]:
    """The actions the node lists as live for the session ``name`` of the demo board, carried by the node itself (an approval wrote them)."""
    listed = device.call("grants", BOARD, device.token)["grants"]
    return sorted(g["action"] for g in listed if g["status"] == "live" and g["carried_by"] == "node" and g["to"].get("name") == name)


def _on(world: World, device: Device) -> str:
    """The whole scenario on one device; what it showed."""
    person = world.person
    device.wire("phone-listen", "--state", device.state_text)
    device.stop_node()
    device.start_node()
    token = device.admin_session()[0]
    assert device.call("phone_info")["listening"], f"{device.name}'s node does not listen for phones after its restart"

    phone = PhoneKeys(f"phone of {device.name}")
    code = device.wire("phone-offer", "--state", device.state_text)["result"]["secret"]
    added = _phone(device, "add", body=phone.add_body(str(device.hello()["device"]), code))
    assert added["ok"] and added["result"]["state"] == "pending", f"{device.name}: a phone added after a key exists should wait for approval: {added}"
    phone.token = added["result"]["token"]
    waiting = next(a for a in device.call("approval_pending", "", token)["approvals"] if a["id"] == added["result"]["approval"])
    assert "POOL: SECURE." in waiting["text"], f"{device.name}: adding a phone states the pool's policy:\n{waiting['text']}"
    approval = grants.sign_approval(person.key, waiting, "approve")
    approved = device.call("approval_decide", "", token, **approval)
    assert approved["state"] == "approved", f"{device.name}: the person's key did not approve the phone: {approved}"
    assert len(device.call("person_keys")["keys"]) == 2, f"{device.name}: the phone's key is not a person key"
    assert _phone(device, "status", phone.token)["result"]["state"] == "active"

    asked = device.call("approval_request", BOARD, device.token, asks=[ASK], note="the pool check asks")
    seen = _phone(device, "get", phone.token, {"id": asked["id"]})["result"]
    for label in ("FLAG:", "POOL:", "RISK:", "WHO:", "ASK:", "SCOPE:", "CANNOT:", "WHY:", "DENY:", "EXPIRES:"):
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
    assert stranger["status"] == 401, f"{device.name}: a token the node does not know read the requests: {stranger}"
    return f"{device.name}: a phone was added (approved by the person's key), a request was approved once and denied once, text the node did not write and a second decision were refused, and a token the node never gave read nothing"


def a_phone_approves_what_a_session_asks_on_every_device(world: World) -> str:
    """On each device a phone made of keys is added (a later phone waits for the person's key to approve it), a session's request reads in full with every part, a signature over other text is refused, the request is approved once and its grant is live, a second one is denied, and nothing a session or a stranger sends decides."""
    world.fresh()
    return "; ".join(_on(world, device) for device in world.devices)

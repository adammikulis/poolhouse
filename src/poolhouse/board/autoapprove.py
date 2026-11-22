"""Grants decided by policy at once: this device's key is made and enrolled the first time it is needed, a request for grants an agent
may have is signed on the spot, and one it may not have is withdrawn with the reason. Nothing asks a person and nothing waits for a phone.

The policy is `person_key.agent_refusal`: a process with no agent marker may have every grant the node's own floors allow, an agent every
one except those that end another device's place in the pool, change who governs it, or release what a scan failed. Each decision is audited
with who asked (`poolhouse.caller`).
"""

from __future__ import annotations

import socket
import time
from typing import Any

from poolhouse import caller
from poolhouse.board import approval, grants, person as person_session, person_key, presence
from poolhouse.board.client import Client, Denied
from poolhouse.board.session import Session
from poolhouse.fleet.onboard.signing import SigningKeys

__all__ = ["POOL", "ensure_key", "refusal", "request"]

POOL = "pool"


def refusal(asks: list[approval.Ask]) -> str:
    """Why policy refuses ``asks`` to the caller, or an empty string when it may have them all."""
    if not (marker := caller.marked()):
        return ""
    why = {a.action: person_key.agent_refusal(a.action) for a in asks if person_key.agent_refusal(a.action)}
    if not why:
        return ""
    names = ", ".join(why)
    reasons = "; ".join(f"{action}: {reason}" for action, reason in why.items())
    return f"{names} {'is' if len(why) == 1 else 'are'} refused to an agent ({marker} is set): {reasons}. To get {'it' if len(why) == 1 else 'them'}, {caller.OUTSIDE}."


def ensure_key(client: Client, carrier: Session) -> SigningKeys:
    """This device's person key, made on first use and enrolled on the node when the node holds none; `Denied` when the node holds another."""
    key = person_key.keys(client.state)
    held = grants.listing(carrier).persons
    if not held:
        about = "".join(c for c in socket.gethostname() if c.isascii() and c.isprintable())[:40] or "this device"
        grants.enrol_first(client, carrier.token, key, about, presence.SILENT)
    elif grants.fingerprint(key.public) not in held:
        raise Denied("denied", "this node already holds another key: carry this device's key over with `person-proof` and `person-enroll`")
    return key


def _waiting(carrier: Session, key: SigningKeys) -> dict[str, dict[str, Any]]:
    node = str(carrier.call("hello")["device"])
    signed = grants.sign_list(key, node, int(time.time() * 1000))
    return {a["id"]: a for a in carrier.call("approval_pending", **signed)["approvals"]}


def request(session: Session, asks: list[approval.Ask], **wire: Any) -> approval.Approval:
    """Ask for ``asks`` and have the answer at once: refused with the reason before anything is raised when policy does not allow them, else
    made and approved with this device's key. Nothing is left pending."""
    if why := refusal(asks):
        caller.record("approval.refused", asks=[a.action for a in asks], why=why)
        raise Denied("denied", why)
    made = approval.request(session, approval.wire(asks, **wire))
    return _settle(session, made, asks)


def _settle(session: Session, made: approval.Approval, asks: list[approval.Ask]) -> approval.Approval:
    """Approve the pending ``made`` request with this device's key."""
    if made.state != approval.PENDING:
        return made
    client = session.client
    carrier = person_session.on(client, POOL)
    key = ensure_key(client, carrier)
    request = _waiting(carrier, key)[made.id]
    context = dict(request.get("challenge", {}).get("context", {}))
    decision = "approve_repeat" if context.get("repeat") else "approve"
    signed = grants.sign_approval(key, request, decision)
    if context.get("pick"):
        signed["pick"] = made.code
    carrier.call("approval_decide", **signed)
    caller.record("approval.approved", id=made.id, asks=[a.action for a in asks])
    made.refresh()
    return made

"""Grants decided by policy at once: this device's key is made and enrolled the first time it is needed, a request for grants an agent
may have is signed on the spot, and one it may not have is refused with the reason before any request exists. Nothing asks a person, nothing
waits for a key and nothing is left pending: a request that cannot be decided here is withdrawn in the same call.

The policy is `person_key.agent_refusal`: a process with no agent marker may have every grant the node's own floors allow, an agent every
one except those that end another device's place in the pool, change who governs it, or release what a scan failed. A device in a pool
counts a grant that reaches beyond the device only from an admin key of the pool, which this device's own key is not, so those are refused
at once as well (`grants.POOL_GRANTS`). Each decision is audited with who asked (`poolhouse.caller`).
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from poolhouse import caller
from poolhouse.board import approval, grants, person as person_session, person_key
from poolhouse.board.client import Client, Denied, NodeError
from poolhouse.board.session import Session
from poolhouse.fleet.onboard.signing import SigningKeys

__all__ = ["POOL", "ensure_key", "refusal", "request"]

POOL = "pool"
PENDING = "pending"
"""The state the node gives a request it has not heard an answer to: it never leaves this module, since a request is decided before it is returned."""


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
    return person_key.enrolled(client, carrier)


def _waiting(carrier: Session, key: SigningKeys) -> dict[str, dict[str, Any]]:
    node = str(carrier.call("hello")["device"])
    signed = grants.sign_list(key, node, int(time.time() * 1000))
    return {a["id"]: a for a in carrier.call("approval_pending", **signed)["approvals"]}


def request(session: Session, asks: list[approval.Ask], **wire: Any) -> approval.Approval:
    """Ask for ``asks`` and have the decision at once: refused with the reason, before any request exists, when policy does not allow them or this
    device's key cannot be made ready; else made and approved with this device's key. A request that could not be carried through is withdrawn,
    so nothing is left pending."""
    if why := refusal(asks):
        caller.record("approval.refused", asks=[a.action for a in asks], why=why)
        raise Denied("denied", why)
    if why := grants.listing(session).pool_refusal([a.action for a in asks]):
        caller.record("approval.refused", asks=[a.action for a in asks], why=why)
        raise Denied("denied", why)
    client = session.client
    carrier = person_session.on(client, POOL)
    key = ensure_key(client, carrier)
    made = dict(session.call("approval_request", **approval.wire(asks, **wire)))
    if made["state"] == PENDING:
        try:
            _decide(carrier, key, made, asks)
        except (NodeError, KeyError, ValueError, RuntimeError):
            with contextlib.suppress(NodeError):
                session.call("approval_cancel", id=made["id"])
            raise
        made = dict(session.call("approval_status", id=made["id"]))
    return approval.Approval.of(made)


def _decide(carrier: Session, key: SigningKeys, made: dict[str, Any], asks: list[approval.Ask]) -> None:
    """Approve the request ``made`` with this device's key."""
    request = _waiting(carrier, key)[made["id"]]
    context = dict(request.get("challenge", {}).get("context", {}))
    signed = grants.sign_approval(key, request, "approve_repeat" if context.get("repeat") else "approve")
    if context.get("pick"):
        signed["pick"] = made.get("code", "")
    carrier.call("approval_decide", **signed)
    caller.record("approval.approved", id=made["id"], asks=[a.action for a in asks])

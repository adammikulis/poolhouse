"""The `approval` command of `poolhouse-workspace`: ask for grants and read what became of the request.

`request` is decided at once by policy (`poolhouse.board.autoapprove`): this device's key signs what the caller may have and the rest is
refused with the reason; a linked phone only gets a notification. `status`, `wait`, `cancel`, `list` and `pending` are any session's and read only
that session's own requests; `pending --all`, `decide`, `blocks` and `lift` act as the device's key and are refused to an agent
(docs/phone-approval.md).
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from poolhouse.board import approval, autoapprove, grants, person as person_session, person_key
from poolhouse.board.approval import Approval, Ask
from poolhouse.board.client import Client, Denied
from poolhouse.board.session import Session
from poolhouse.caller import refuse_agent
from poolhouse.command import flag
from poolhouse.log import say
from poolhouse.workspace import board_cli

__all__ = ["COMMAND", "OPTIONS", "POOL", "answer", "held_key"]

POOL = "pool"
VERBS = ("request", "status", "wait", "cancel", "list", "pending", "decide", "blocks", "lift")
OPTIONS = [
    flag("verb", choices=VERBS, help="request ACTION[:RESOURCE]..., status ID, wait ID, cancel ID, list, pending, blocks, lift BOARD/SESSION, or decide ID approve|deny|block"),
    flag("items", nargs="*", help="what the verb takes: asks for request, a handle for the others, then approve, deny or block for decide"),
    flag("--for", dest="lasts", default="1h", metavar="TIME", help="request: how long each grant lasts once approved: 90m, 12h, 30d (default 1h)"),
    flag("--uses", type=int, default=0, help="request: how often each may be used (default: as often as it is needed)"),
    flag("--review", action="store_true", help="request: each use needs an independent review behind it"),
    flag("--note", default="", help="request: your own words for the notification, up to 160 plain characters (shown as yours, checked by nobody)"),
    flag("--task", default="", metavar="REF", help="request: the task on the board the request came from"),
    flag("--ttl", type=int, default=0, metavar="SECONDS", help="request: how long it stays pending (default 30 minutes, at most 24 hours)"),
    flag("--expect-policy", default="", choices=("", "open", "secure"), help="request: what you believe the pool's join policy is; the node refuses the request if it is not"),
    flag("--all", action="store_true", help="pending: every request that waits, not only your own (refused to an agent: your key signs to read them)"),
    flag("--pick", default="", help="decide: the number the asker was shown, for a request that is not routine"),
    flag("--timeout", type=float, default=30.0, metavar="SECONDS", help="wait: how long to wait for the answer"),
]
OK = ("pending", "approved")


def _asks(args: argparse.Namespace) -> list[Ask]:
    if not args.items:
        raise ValueError("name what to ask for: ACTION or ACTION:RESOURCE, for example add_device or link_write:demo>lab")
    return [Ask(action, resource or "*", args.lasts, args.uses or None, args.review) for action, _, resource in (i.partition(":") for i in args.items)]


def _shown(a: Approval) -> dict[str, Any]:
    shown: dict[str, Any] = {"id": a.id, "state": a.state, "message": a.message(), "risk": a.risk, "expires_ms": a.expires_ms, "grants": list(a.grants), "text": a.text,
                             "deduplicated": a.deduplicated}
    if a.code:
        shown["code"] = a.code
    return shown


def _print(args: argparse.Namespace, a: Approval) -> int:
    if args.json:
        say(json.dumps(_shown(a)))
    else:
        say(f"{a.id}  {a.state}: {a.message()}")
        if a.deduplicated:
            say("this was already waiting: asking again is the same request, and nothing new reaches your person")
        if a.state == approval.PENDING:
            say(f"it ends {grants.when(a.expires_ms)}; to wait for the answer: poolhouse-workspace approval wait {a.id}")
        if a.code:
            say(f"\nSHOW YOUR PERSON THIS NUMBER: {a.code}\nthey choose it among three on their phone after their fingerprint; tell it to them where they read you, and nowhere else")
        say("\nwhat the person reads:\n" + a.text)
    return 0 if a.state in OK else 3


def _handle(args: argparse.Namespace, s: Session) -> Approval:
    if not args.items:
        raise ValueError("name the request: its handle, `ap-` and 16 hex digits")
    return Approval(s, dict(s.call("approval_status", id=args.items[0])))


def _own(args: argparse.Namespace, s: Session) -> int:
    if args.verb == "blocks":
        if args.items:
            raise ValueError("blocks takes no arguments")
        found = approval.blocks(s)
        say(json.dumps(found) if args.json else "\n".join(f"{who}  until {grants.when(until)}" for who, until in found.items()) or "(none)")
        return 0
    if args.verb == "lift":
        if len(args.items) != 1:
            raise ValueError("lift takes the blocked board/session")
        approval.lift_at_computer(s, args.items[0])
        say(json.dumps({"lifted": True}) if args.json else "The block was lifted.")
        return 0
    if args.verb == "request":
        asks = _asks(args)
        made = approval.request(s, approval.wire(asks, note=args.note, task=args.task, ttl_s=args.ttl, expect_policy=args.expect_policy))
        made = autoapprove.settle(s, made, asks)
        return _print(args, made)
    if args.verb == "pending" and args.all:
        refuse_agent("reading every request that waits", "it lists other sessions' requests, which the device's key signs to read")
        carrier, key = held_key(Client())
        listed = list(_waiting(carrier, key).values())
        say(json.dumps(listed) if args.json else "\n".join(f"{a['id']}  {a['state']:<9} {a['risk']:<12} {a['title']}" for a in listed) or "(none)")
        return 0
    if args.verb in ("list", "pending"):
        listed = s.call("approvals" if args.verb == "list" else "approval_pending")["approvals"]
        say(json.dumps(listed) if args.json else "\n".join(f"{a['id']}  {a['state']:<9} {a['risk']:<12} {a['title']}" for a in listed) or "(none)")
        return 0
    got = _handle(args, s)
    if args.verb == "wait":
        got.wait(args.timeout)
    elif args.verb == "cancel":
        got.cancel()
    return _print(args, got)


def held_key(client: Client) -> tuple[Session, Any]:
    """The person's own session of this terminal on the pool board and this device's key, which this node must hold."""
    if not person_key.kept(client.state):
        raise Denied("denied", "no person key is kept on this device: `poolhouse-workspace approval request` makes and enrols it")
    carrier = person_session.on(client, POOL)
    key = person_key.keys(client.state)
    if grants.fingerprint(key.public) not in grants.listing(carrier).persons:
        raise Denied("denied", "this node does not hold your key: run `poolhouse-workspace grants-setup`")
    return carrier, key


def _waiting(carrier: Session, key: Any) -> dict[str, dict[str, Any]]:
    """Every request that waits, by handle, read with the person's key: the key signs the reading (a typed yes), because a session reads only its own."""
    node = str(carrier.call("hello")["device"])
    signed = grants.sign_list(key, node, int(time.time() * 1000))
    return {a["id"]: a for a in carrier.call("approval_pending", **signed)["approvals"]}


def answer(carrier: Session, key: Any, request: dict[str, Any], decision: str, pick: str = "") -> dict[str, Any]:
    """Sign ``decision`` on the ``request`` as the node showed it and give it to the node. A scope that was declined before is
    answered ``approve_repeat``, and a request that is not routine carries ``pick``, the number the asker was shown."""
    context = dict(request.get("challenge", {}).get("context", {}))
    if decision == "approve" and context.get("repeat"):
        decision = "approve_repeat"
    try:
        signed = grants.sign_approval(key, request, decision)
    except RuntimeError as err:
        raise Denied("denied", str(err)) from err
    if decision.startswith("approve") and context.get("pick"):
        if pick not in context["pick"]:
            raise Denied("denied", "this request carries a number the asker was shown: pass it with --pick (" + " / ".join(context["pick"]) + ")")
        signed["pick"] = pick
    return dict(carrier.call("approval_decide", **signed))


def _decide(args: argparse.Namespace) -> int:
    """Answer a request with this device's key."""
    refuse_agent("answering an approval", "an agent asks with `approval request`, which policy decides at once")
    if len(args.items) != 2 or args.items[1] not in ("approve", "deny", "block"):
        raise ValueError("decide takes a handle and approve, deny or block: `approval decide ap-0123456789abcdef approve`")
    carrier, key = held_key(Client())
    waiting = _waiting(carrier, key)
    if args.items[0] not in waiting:
        raise ValueError("no request waiting has that handle: `poolhouse-workspace approval pending --all` lists them")
    done = answer(carrier, key, waiting[args.items[0]], args.items[1], args.pick)
    say(json.dumps({"id": done["id"], "state": done["state"]}) if args.json else f"{done['id']}  {done['state']}")
    return 0


def command(args: argparse.Namespace) -> int:
    """The `approval` command: `decide`, or any session's own requests."""
    if args.verb == "decide":
        return _decide(args)
    if args.verb in ("blocks", "lift"):
        refuse_agent("managing approval blocks", "a block ends a session's repeated requests, and an agent must not lift its own")
        return _own(args, person_session.on(Client(), POOL))
    return _own(args, board_cli.connect(args))


COMMAND = board_cli.guarded(command)

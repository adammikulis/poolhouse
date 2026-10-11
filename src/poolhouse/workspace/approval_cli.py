"""The `approval` command of `poolhouse-workspace`: ask a person's phone for grants, wait for the answer and read what became of it.

`request`, `status`, `wait`, `cancel`, `list` and `pending` are any session's. `decide` is a person's at a terminal: it signs the
answer with the person's key after a typed yes, the other way a person's key answers (the phone is the first). What an agent can do
is ask and wait; no verb here answers for a person (docs/phone-approval.md).
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from poolhouse import person
from poolhouse.board import approval, grants, person as person_session, person_key
from poolhouse.board.approval import Approval, Ask
from poolhouse.board.client import Client, Denied
from poolhouse.board.session import Session
from poolhouse.command import flag
from poolhouse.log import say
from poolhouse.workspace import board_cli

__all__ = ["COMMAND", "OPTIONS", "POOL", "answer", "held_key"]

POOL = "pool"
VERBS = ("request", "status", "wait", "cancel", "list", "pending", "decide")
OPTIONS = [
    flag("verb", choices=VERBS, help="request ACTION[:RESOURCE]..., status ID, wait ID, cancel ID, list, pending, or (a person) decide ID approve|deny"),
    flag("items", nargs="*", help="what the verb takes: asks for request, a handle for the others, then approve or deny for decide"),
    flag("--for", dest="lasts", default="1h", metavar="TIME", help="request: how long each grant lasts once approved: 90m, 12h, 30d (default 1h)"),
    flag("--uses", type=int, default=0, help="request: how often each may be used (default: as often as it is needed)"),
    flag("--review", action="store_true", help="request: each use needs an independent review behind it"),
    flag("--note", default="", help="request: your own words for the person, up to 160 plain characters (shown as yours, checked by nobody)"),
    flag("--task", default="", metavar="REF", help="request: the task on the board the request came from"),
    flag("--ttl", type=int, default=0, metavar="SECONDS", help="request: how long it stays on the person's phone (default 30 minutes, at most 24 hours)"),
    flag("--expect-policy", default="", choices=("", "open", "secure"), help="request: what you believe the pool's join policy is; the node refuses the request if it is not"),
    flag("--wait", type=float, default=0.0, metavar="SECONDS", help="request: wait this long for the answer"),
    flag("--timeout", type=float, default=30.0, metavar="SECONDS", help="wait: how long to wait for the answer"),
]
OK = ("pending", "approved")


def _asks(args: argparse.Namespace) -> list[Ask]:
    if not args.items:
        raise ValueError("name what to ask for: ACTION or ACTION:RESOURCE, for example add_device or link_write:demo>lab")
    return [Ask(action, resource or "*", args.lasts, args.uses or None, args.review) for action, _, resource in (i.partition(":") for i in args.items)]


def _shown(a: Approval) -> dict[str, Any]:
    return {"id": a.id, "state": a.state, "message": a.message(), "risk": a.risk, "expires_ms": a.expires_ms, "grants": list(a.grants), "text": a.text}


def _print(args: argparse.Namespace, a: Approval) -> int:
    if args.json:
        say(json.dumps(_shown(a)))
    else:
        say(f"{a.id}  {a.state}: {a.message()}")
        if a.state == approval.PENDING:
            say(f"it ends {grants.when(a.expires_ms)}; to wait for the answer: poolhouse-workspace approval wait {a.id}")
        say("\nwhat the person reads:\n" + a.text)
    return 0 if a.state in OK else 3


def _handle(args: argparse.Namespace, s: Session) -> Approval:
    if not args.items:
        raise ValueError("name the request: its handle, `ap-` and 16 hex digits")
    return Approval(s, dict(s.call("approval_status", id=args.items[0])))


def _own(args: argparse.Namespace, s: Session) -> int:
    if args.verb == "request":
        made = approval.request(s, approval.wire(_asks(args), note=args.note, task=args.task, ttl_s=args.ttl, expect_policy=args.expect_policy))
        if args.wait:
            made.wait(args.wait)
        return _print(args, made)
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
    """The person's own session of this terminal on the pool board and the person's key, which this node must hold."""
    if not person_key.kept(client.state):
        raise Denied("denied", "no person key is kept on this device: run `poolhouse-workspace grants-setup`, or answer on your phone")
    carrier = person_session.on(client, POOL)
    key = person_key.keys(client.state)
    if grants.fingerprint(key.public) not in grants.listing(carrier).persons:
        raise Denied("denied", "this node does not hold your key: run `poolhouse-workspace grants-setup`")
    return carrier, key


def answer(carrier: Session, key: Any, request: dict[str, Any], decision: str) -> dict[str, Any]:
    """Sign ``decision`` on the ``request`` as the node showed it, once the person has read its text and typed yes, and give it to the node."""
    try:
        signed = grants.sign_approval(key, request, decision)
    except RuntimeError as err:
        raise Denied("denied", str(err)) from err
    return dict(carrier.call("approval_decide", **signed))


def _decide(args: argparse.Namespace) -> int:
    """Answer a request with the person's key, after the person has read its text and typed yes."""
    person.require_person("answering an approval")
    if len(args.items) != 2 or args.items[1] not in ("approve", "deny"):
        raise ValueError("decide takes a handle and approve or deny: `approval decide ap-0123456789abcdef approve`")
    carrier, key = held_key(Client())
    waiting = {a["id"]: a for a in carrier.call("approval_pending")["approvals"]}
    if args.items[0] not in waiting:
        raise ValueError("no request waiting has that handle: `poolhouse-workspace approval pending` lists them")
    done = answer(carrier, key, waiting[args.items[0]], args.items[1])
    say(json.dumps({"id": done["id"], "state": done["state"]}) if args.json else f"{done['id']}  {done['state']}")
    return 0


def command(args: argparse.Namespace) -> int:
    """The `approval` command: a person's `decide`, or any session's own requests."""
    return _decide(args) if args.verb == "decide" else _own(args, board_cli.connect(args))


COMMAND = board_cli.guarded(command)

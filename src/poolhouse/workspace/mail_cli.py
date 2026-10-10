"""The conversation commands of `poolhouse-workspace`: dm, thread, ack, wait, outbox and the held entries."""

from __future__ import annotations

import argparse
import time
from typing import Any

from poolhouse.board.session import Entry, Session
from poolhouse.command import flag
from poolhouse.person import require_person
from poolhouse.workspace import inbound, limits, render
from poolhouse.workspace.board_cli import TYPES, screened
from poolhouse.workspace.boardapi import Held
from poolhouse.workspace.cli_options import READ, WIDEN
from poolhouse.workspace.screen import Refused

__all__ = ["TABLE"]

POLL_S = 0.5


def _cut(rows: list[Entry], take: int) -> list[Entry]:
    """``rows`` as at most ``take``: the first and the newest."""
    if len(rows) <= take:
        return rows
    return [rows[0], *rows[-(take - 1):]] if take > 1 else rows[:1]


def thread(args: argparse.Namespace, s: Session) -> Any:
    """A message and its replies: the first and the newest by default."""
    lim = limits.load(limits.root())
    rows = s.read_all(thread=args.root)
    take = len(rows) if args.all else args.limit if args.limit > 0 else lim.read_items
    kept = _cut(rows, take)
    known, records = inbound.names(s), inbound.hold(s, kept)
    out = Held(inbound.shown(e, known, 0 if args.all else lim.board_message_chars, records) for e in kept)
    out.held = len(rows) - len(out)
    return out


def _place(s: Session, ref: str) -> tuple[str, int]:
    """The log and sequence number a message reference names: a bare number is one of this device's."""
    parts = ref.split(":")
    if ref.isdigit() and int(ref) > 0:
        return s.origin(), int(ref)
    if len(parts) == 3 and parts[2].isdigit() and int(parts[2]) > 0:
        return parts[1], int(parts[2])
    raise ValueError("ack takes the number of a message, or its id")


def ack(args: argparse.Namespace, s: Session) -> Any:
    """Mark messages up to a number (of this device) or an id read; never moves back."""
    origin, number = _place(s, args.ref)
    before = s.cursor("inbox")
    now = max(before.get(origin, 0), number)
    s.keep("inbox", {**before, origin: now})
    return {"cursor": now}


def wait(args: argparse.Namespace, s: Session) -> Any:
    """Block until a message arrives or the time runs out."""
    deadline = time.monotonic() + args.timeout
    while True:
        page = s.read(s.cursor("inbox"), inbox=True)
        left = deadline - time.monotonic()
        if page.entries or left <= 0:
            break
        time.sleep(min(POLL_S, left))
    out = inbound.present(s, page.entries, args.limit, args.all, args.raw)
    if args.ack and out:
        s.keep("inbox", inbound.advanced(page.entries[:len(out)], s.cursor("inbox")))
    return out


def outbox(args: argparse.Namespace, s: Session) -> Any:
    """The messages the caller sent, each provisional until every device that syncs the board holds it."""
    known = inbound.names(s)
    return [{**inbound.shown(sent.entry, known), "raw": sent.entry.text, "sync": sent.status} for sent in s.outbox()]


def conversations(s: Session) -> list[dict[str, Any]]:
    """Every direct conversation of the caller: the join, how many messages, the latest and how many are unread."""
    joins: dict[str, dict[str, Any]] = {}
    seen = {}
    for e in s.read_all(channel="#dm"):
        other = str(e.fields.get("to")) if e.sender == s.name else e.sender
        seen.setdefault(other, s.cursor(f"dm:{other}"))
        a, b = sorted((s.name, other))
        p = joins.setdefault(other, {"a": a, "b": b, "messages": 0, "last": 0.0, "unread": 0})
        p["messages"] += 1
        p["last"] = e.at_ms / 1000
        p["unread"] += e.sender != s.name and e.number > seen[other].get(e.origin, 0)
    return sorted(joins.values(), key=lambda p: p["last"], reverse=True)


def dm(args: argparse.Namespace, s: Session) -> Any:
    """The two-sided conversation with NAME, sending BODY first when given; with no NAME, the conversations."""
    if not args.name:
        return conversations(s)
    if args.body:
        if args.type not in TYPES or args.type == "file":
            raise ValueError(f"type must be one of {', '.join(t for t in TYPES if t != 'file')}")
        body = render.body(args.body)
        screened("the message", args.subject, body)
        s.post(args.name, args.type, body, subject=args.subject, reply_id=args.reply_to)
    rows = s.read_all(conversation=args.name)[-max(args.limit, 0):]
    known, records = inbound.names(s), inbound.hold(s, rows)
    out = [{**inbound.shown(e, known, 0, records), "direction": "sent" if e.sender == s.name else "received"} for e in rows]
    received = [e for e in rows if e.sender != s.name]
    if received:
        s.keep(f"dm:{args.name}", inbound.advanced(received, s.cursor(f"dm:{args.name}")))
    return out


def held(args: argparse.Namespace, s: Session) -> Any:
    """The messages held back for flagged text, without their text."""
    return s.held()


def free(args: argparse.Namespace, s: Session) -> Any:
    """Let a held message be shown, fenced; a person at a terminal only."""
    require_person("quarantine-release")
    item = s.free(args.qid)
    if item["state"] != "released":
        raise Refused(f"{args.qid} was not released")
    return {"released": args.qid, "text_chars": item["chars"]}


TABLE = (
    ("dm", "the two-sided conversation with NAME (BODY sends first); no NAME lists them", [
        flag("name", nargs="?", default=""), flag("body", nargs="?", default=""),
        flag("--type", choices=TYPES, default="note"), flag("--subject", default=""),
        flag("--reply-to", default="", help="the number of the message this answers"),
        flag("--limit", type=int, default=50)], dm),
    ("thread", "a message and its replies (first and newest by default)", [flag("root"), *WIDEN], thread),
    ("ack", "mark messages up to NUMBER (or an id) read", [flag("ref")], ack),
    ("wait", "block until a message arrives", [*READ, flag("--timeout", type=float, default=60.0)], wait),
    ("outbox", "messages you sent, each provisional until every device that syncs the board holds it", [], outbox),
    ("quarantine-ls", "messages held back for flagged text", [], held),
    ("quarantine-release", "show a held message, fenced; a person at a terminal", [flag("qid")], free),
)

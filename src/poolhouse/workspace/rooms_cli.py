"""The room and subscription commands of `poolhouse-workspace`: board, join-board, leave-board, subscribe, subs, watch."""

from __future__ import annotations

import argparse
import contextlib
import sys
import time
from typing import Any

from poolhouse.board.client import NodeError
from poolhouse.board.session import Entry, Session
from poolhouse.command import flag
from poolhouse.errors import Denied
from poolhouse.workspace import board_cli, inbound, limits, render
from poolhouse.workspace.board_cli import ANNOUNCE, TYPES
from poolhouse.workspace.cli_options import FOR_AGENT, WIDEN
from poolhouse.workspace.inbound import Held
from poolhouse.workspace.plain import data_line

__all__ = ["ACTIONS", "TABLE", "watching"]

ACTIONS = ("list", "read", "post", "threads", "create", "add", "mentions")
STYPES = ("board", "thread", "agent", "kind", "mentions")
MODES = ("inbox", "digest", "silent")
POLL_S = 0.5


def _room(s: Session, name: str) -> dict[str, Any]:
    """The room ``name`` as this session sees it; `Denied` when it is not one it may read."""
    found = next((r for r in s.rooms() if r["name"] == name and r["member"]), None)
    if found is None:
        raise Denied(f"no board {data_line(name, 48)} that {s.name} belongs to")
    return found


def _quiet_digest(s: Session, name: str) -> None:
    """Follow a room's news in the digest when the session has room for one more subscription."""
    try:
        s.subscribe("board", name, "digest")
    except NodeError:
        return


def _unread(s: Session, name: str, entries: list[Entry]) -> int:
    seen = s.cursor(f"room:{name}")
    return sum(1 for e in entries if e.sender != s.name and e.number > seen.get(e.origin, 0))


def listing(s: Session) -> list[dict[str, Any]]:
    out = []
    for r in s.rooms():
        mine = s.read_all(channel=r["name"]) if r["member"] else []
        out.append({"name": r["name"], "title": data_line(r["title"], 60), "project": False, "open": r["open"],
                    "created_by": r["created_by"], "members": r["members"], "member": r["member"], "posts": r["posts"],
                    "last": r["last_ms"] / 1000, "unread": _unread(s, r["name"], mine)})
    return out


def _read(args: argparse.Namespace, s: Session) -> Any:
    _room(s, args.name)
    lim = limits.load(limits.root())
    since = {s.origin(): args.after} if args.after else None
    rows = s.read_all(since, channel=args.name)
    newest = rows[-(args.limit if args.limit > 0 else lim.read_items):]
    out = Held()
    known, used = inbound.names(s), 0
    records = inbound.hold(s, newest)
    for e in newest:
        item = {**inbound.shown(e, known, 0 if args.limit > 0 else lim.read_item_chars, records), "board": e.channel}
        used += len(item["text"])
        if used > (lim.board_read_chars if args.limit > 0 else lim.read_total_chars) and out:
            break
        out.append(item)
    out.held = len(rows) - len(out)
    if not args.no_mark and out:
        s.keep(f"room:{args.name}", inbound.advanced(rows, s.cursor(f"room:{args.name}")))
    return out


def _threads(args: argparse.Namespace, s: Session) -> Any:
    _room(s, args.name)
    found: dict[str, dict[str, Any]] = {}
    seen = s.cursor(f"room:{args.name}")
    for e in s.read_all(channel=args.name):
        root = str(e.fields.get("thread_id") or e.id)
        t = found.setdefault(root, {"root": root, "subject": "(earlier message pruned)", "from": "", "started": 0.0, "last": 0.0,
                                    "replies": 0, "unread": 0})
        if e.id == root:
            t.update(root=e.ref, subject=data_line(e.fields.get("subject") or e.text, 120), started=e.at_ms / 1000, **{"from": e.sender})
        else:
            t["replies"] += 1
        t["last"] = max(t["last"], e.at_ms / 1000)
        t["unread"] += e.sender != s.name and e.number > seen.get(e.origin, 0)
    ranked = sorted(found.values(), key=lambda t: t["last"], reverse=True)
    return [{**t, "authority": "none"} for t in ranked[:max(args.limit or 50, 0)]]


def _mentions(args: argparse.Namespace, s: Session) -> Any:
    rows = s.read_all(mentions=True)[-max(args.limit or 50, 0):]
    known, records = inbound.names(s), inbound.hold(s, rows)
    return [{**inbound.shown(e, known, 0, records), "board": e.channel} for e in rows]


def board(args: argparse.Namespace, s: Session) -> Any:
    """Rooms: list, read NAME, post NAME TEXT, threads NAME, create NAME [TITLE], add NAME AGENT, mentions."""
    name, rest = args.name, args.rest
    if args.action == "list":
        return listing(s)
    if args.action == "mentions":
        return _mentions(args, s)
    if not name:
        raise ValueError(f"board {args.action} needs a board name such as #general")
    if args.action == "read":
        return _read(args, s)
    if args.action == "threads":
        return _threads(args, s)
    if args.action == "create":
        made = s.room("create", name, title=" ".join(rest), private=args.private)
        _quiet_digest(s, name)
        return {"board": name, "open": made["open"]}
    if not rest:
        raise ValueError(f"board {args.action} needs one more argument")
    if args.action == "add":
        s.room("add", name, who=rest[0])
        return {"added": rest[0], "to": name}
    if name == ANNOUNCE:
        raise ValueError(f"{ANNOUNCE} takes only `announce KIND TEXT` (joined, milestone, done, blocked; one line)")
    return board_cli.send(argparse.Namespace(to=name, type=args.type, body=" ".join(rest), subject=args.subject,
                                             reply_to=args.reply_to), s)


def join(args: argparse.Namespace, s: Session) -> Any:
    """Join an open room."""
    s.room("join", args.name)
    _quiet_digest(s, args.name)
    return {"joined": args.name}


def leave(args: argparse.Namespace, s: Session) -> Any:
    """Leave a room; its subscription goes with it."""
    s.room("leave", args.name)
    with contextlib.suppress(NodeError):
        s.unsubscribe("board", args.name)
    return {"left": args.name}


def subscribe(args: argparse.Namespace, s: Session) -> Any:
    """Choose what reaches your inbox: a room, thread, agent, kind or mentions."""
    return s.subscribe(args.type, args.target, args.mode, args.force)


def unsubscribe(args: argparse.Namespace, s: Session) -> Any:
    """Drop a subscription; messages stay."""
    return s.unsubscribe(args.type, args.target)


def subs(args: argparse.Namespace, s: Session) -> Any:
    """Your subscriptions."""
    return [{"type": x["type"], "target": data_line(x["target"], 60), "mode": x["mode"]} for x in s.subs()]


def _batch(args: argparse.Namespace, s: Session, cursor: dict[str, int], scope: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The next unseen messages of what is watched and the cursor after them."""
    if scope:
        page = s.read(cursor, limit=50, **scope)
        return inbound.present(s, page.entries, args.limit, args.all), page.cursor
    page = s.read(s.cursor("inbox"), inbox=True)
    shown = inbound.present(s, page.entries, args.limit, args.all)
    if shown:
        s.keep("inbox", inbound.advanced(page.entries[:len(shown)], s.cursor("inbox")))
    return shown, cursor


def watching(args: argparse.Namespace) -> int:
    """Print each batch of new messages as it arrives; 0 after one batch with --once, 3 when the timeout passes."""
    s = board_cli.connect(args)
    scope = {k: v for k, v in (("channel", args.room), ("thread", args.thread), ("conversation", args.dm)) if v}
    if len(scope) > 1:
        raise ValueError("watch one of --room, --thread or --dm")
    if args.room:
        _room(s, args.room)
    cursor = (s.head() if args.since < 0 else {s.origin(): args.since}) if scope else {}
    began = time.monotonic()
    while True:
        left = args.timeout - (time.monotonic() - began) if args.timeout else POLL_S
        if args.timeout and left <= 0:
            return 3
        batch, cursor = _batch(args, s, cursor, scope)
        for item in batch:
            render.show(args, item)
            sys.stdout.flush()
        render.held_note(batch)
        if batch and args.once:
            return 0
        time.sleep(min(POLL_S, left))


TABLE = (
    ("board", "boards: list, read NAME, post NAME TEXT, threads NAME, create NAME [TITLE], add NAME AGENT, mentions", [
        flag("action", choices=ACTIONS), flag("name", nargs="?", default=""),
        flag("rest", nargs="*"), flag("--type", choices=TYPES, default="note"),
        flag("--subject", default=""), flag("--reply-to", default=""),
        flag("--limit", type=int, default=0, help="read: how many (default: a few, cut short)"),
        flag("--after", type=int, default=0),
        flag("--private", action="store_true", help="create: only people you add can join"),
        flag("--no-mark", action="store_true", help="read: leave the board's unread count"), FOR_AGENT], board),
    ("join-board", "join an open board", [flag("name")], join),
    ("leave-board", "leave a board", [flag("name")], leave),
    ("subscribe", "choose what reaches your inbox: board, thread, agent, kind or mentions", [
        flag("type", choices=STYPES), flag("target", nargs="?", default=""),
        flag("--mode", choices=MODES, default="inbox",
             help="inbox, digest (summarised by `digest`) or silent (kept, never delivered)"),
        flag("--force", action="store_true", help="accept more than a few inbox subscriptions, each one more context")],
     subscribe),
    ("unsubscribe", "drop a subscription; messages stay", [flag("type", choices=STYPES), flag("target", nargs="?", default="")],
     unsubscribe),
    ("subs", "your subscriptions", [], subs),
)
WATCH = [*WIDEN, flag("--once", action="store_true"),
         flag("--room", default="", help="follow one room without a subscription"),
         flag("--thread", default="", help="follow one thread"),
         flag("--dm", default="", help="follow your conversation with this agent"),
         flag("--since", type=int, default=-1,
              help="with --room/--thread/--dm: start after this message (default: from now)"),
         flag("--timeout", type=float, default=0.0, help="stop after this many seconds (exit 3 if nothing came)")]

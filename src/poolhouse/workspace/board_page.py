"""The person's Board page as the ml-board element reads it, answered from the person's session on the node.

The element counts messages with whole numbers. The node orders every message of a board by one clock, so a message's
number here is its time in milliseconds times 4096 plus its tick, moved up past the number before it when two share
one: the same for every device reading the same messages, and growing with the board's order.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from poolhouse.board import client
from poolhouse.board.session import Agent, Entry, Session
from poolhouse.workspace import board_pages, filecli, inbound, plain
from poolhouse.workspace.agent_display import describe
from poolhouse.workspace.boardapi import data_line
from poolhouse.workspace.screen import Refused

__all__ = ["Log", "ack", "agents", "boards", "conversations", "dm", "file", "head", "post", "read", "thread", "threads", "wait"]

TICKS = 4096
MARK = "ui:"
GENERAL, ANNOUNCE, DIRECT = "#general", "#announcements", "#dm"
BODY_CHARS = 4000
SUBJECT_CHARS = 200
POLL_S = 0.05
KINDS = ("task", "status", "handoff", "question", "answer", "note")


class Log:
    """The messages of one question, numbered for the page."""

    def __init__(self, entries: list[Entry]) -> None:
        self.entries = sorted((e for e in entries if e.kind == "message"), key=lambda e: (e.at_ms, e.counter, e.id))
        self.number: dict[str, int] = {}
        last = 0
        for e in self.entries:
            last = self.number[e.id] = max(e.at_ms * TICKS + min(e.counter, TICKS - 1), last + 1)
        self.by_number = {self.number[e.id]: e for e in self.entries}

    def of(self, ref: str) -> int:
        """The number of the message with id ``ref`` (0 when it is not here)."""
        return self.number.get(ref, 0)

    def thread(self, e: Entry) -> int:
        """The number of the message that starts the thread ``e`` is in."""
        return self.of(str(e.fields.get("thread_id") or e.id)) or self.number[e.id]

    def rows(self) -> list[dict[str, Any]]:
        return [{"seq": self.number[e.id], "entry": e} for e in self.entries]


def _kind(agent: Agent | None, name: str) -> dict[str, Any]:
    if agent is None:
        return {"display_name": data_line(name, 80), "session_kind": "unknown", "coordinator_eligible": False,
                "coordinator_reason": "not a session of this board", "spawned_by": ""}
    return describe(agent)


def _plain(log: Log, who: Mapping[str, Agent], row: dict[str, Any]) -> dict[str, Any]:
    e: Entry = row["entry"]
    body, cut = plain.text(e.text, BODY_CHARS)
    agent = who.get(e.sender.split("@")[0])
    found = {"seq": row["seq"], "type": str(e.fields.get("type", "note")), "from": e.sender, **_kind(agent, e.sender),
             "role": "agent", "to": str(e.fields.get("to", e.channel)), "ts": e.at_ms / 1000, "thread": log.thread(e),
             "reply_to": log.of(str(e.fields.get("reply_id", ""))), "mentions": [],
             "subject": plain.line(str(e.fields.get("subject", "")), SUBJECT_CHARS), "body": body,
             "held": inbound.judge(e)[2], "truncated": cut, "model": agent.model if agent else "",
             "model_state": agent.model_state if agent else ""}
    meta = e.fields.get("file")
    if isinstance(meta, dict):
        found["file"] = {k: meta[k] for k in ("id", "name", "size", "type", "text")}
    return found


def _marks(s: Session, key: str) -> int:
    return int(s.cursor(MARK + key).get("through", 0))


def _channel(s: Session, name: str) -> Log:
    """The messages of a room; the person joins an open room by looking at it."""
    if name not in (GENERAL, ANNOUNCE):
        found = next((r for r in s.rooms() if r["name"] == name), None)
        if found is None:
            raise client.Denied("denied", f"no board {plain.line(name, 48)} that {s.name} belongs to")
        if found["open"] and not found["member"]:
            s.room("join", name)
    return Log(s.read_all(channel=name, kind="message"))


def boards(s: Session) -> dict[str, Any]:
    """The rooms the person may see, with who is in them, their posts and what is unread."""
    out = []
    for room in s.rooms():
        log = Log(s.read_all(channel=room["name"], kind="message")) if room["member"] else Log([])
        mark = _marks(s, room["name"])
        unread = sum(1 for n, e in log.by_number.items() if n > mark and e.sender != s.name)
        out.append({"name": room["name"], "title": data_line(room["title"], 60), "project": False, "open": room["open"],
                    "created_by": room["created_by"], "members": room["members"], "member": room["member"],
                    "posts": room["posts"], "last": room["last_ms"] / 1000, "unread": unread})
    return {"me": s.name, "boards": out}


def agents(s: Session) -> dict[str, Any]:
    """The sessions of the board other than the person's, as the page lists them."""
    return {"owner_id": s.name, "agents": [
        {"id": a.name, "role": "agent", "device": "", "display_name": describe(a)["display_name"],
         "session_kind": describe(a)["session_kind"], "parent": a.parent,
         "coordinator_eligible": describe(a)["coordinator_eligible"], "coordinator_reason": describe(a)["coordinator_reason"]}
        for a in s.agents()[:200] if a.name != s.name]}


def _who(s: Session) -> dict[str, Agent]:
    return inbound.names(s)


def threads(s: Session, board: str, limit: int) -> dict[str, Any]:
    """The threads of a room, latest activity first."""
    log, who, found = _channel(s, board), _who(s), {}
    mark = _marks(s, board)
    for n, e in log.by_number.items():
        root = log.thread(e)
        t = found.setdefault(root, {"root": root, "subject": "(earlier message pruned)", "from": "", "started": 0.0,
                                    "last": 0.0, "replies": 0, "unread": 0})
        if n == root:
            t.update(subject=data_line(str(e.fields.get("subject", "")) or e.text, 120), started=e.at_ms / 1000,
                     **{"from": e.sender, "display_name": _kind(who.get(e.sender), e.sender)["display_name"]})
        else:
            t["replies"] += 1
        t["last"] = max(t["last"], e.at_ms / 1000)
        t["unread"] += n > mark and e.sender != s.name
    ranked = sorted(found.values(), key=lambda t: (t["last"], t["root"]), reverse=True)
    return {"board": board, "threads": [{**t, "authority": "none"} for t in ranked[:max(limit, 0)]]}


def read(s: Session, board: str, window: tuple[int, int, int]) -> dict[str, Any]:
    """A page of the messages of a room."""
    limit, after, before = window
    log, who = _channel(s, board), _who(s)
    return {"board": board, **board_pages.page(log.rows() if limit else [], lambda r: _plain(log, who, r), limit, after, before)}


def thread(s: Session, root: int, window: tuple[int, int, int]) -> dict[str, Any]:
    """A page of one thread: the message numbered ``root`` and every reply to it."""
    log = Log(s.read_all(kind="message"))
    found = log.by_number.get(root)
    if found is None:
        raise ValueError(f"no thread {root}")
    kept = {e.id for e in s.read_all(thread=found.id)}
    limit, after, before = window
    who = _who(s)
    rows = [r for r in log.rows() if r["entry"].id in kept] if limit else []
    return {"root": root, **board_pages.page(rows, lambda r: _plain(log, who, r), limit, after, before)}


def _other(s: Session, a: str, b: str) -> str:
    if s.name not in (a, b) or a == b:
        raise client.Denied("denied", f"{s.name} is not part of that conversation")
    return b if a == s.name else a


def dm(s: Session, a: str, b: str, window: tuple[int, int, int]) -> dict[str, Any]:
    """A page of the conversation between the person and one session."""
    other = _other(s, a, b)
    log, who = Log(s.read_all(conversation=other, kind="message")), _who(s)
    limit, after, before = window
    rows = log.rows() if limit else []

    def show(row: dict[str, Any]) -> dict[str, Any]:
        return {**_plain(log, who, row), "direction": "sent" if row["entry"].sender == a else "received"}
    return {"a": a, "b": b, **board_pages.page(rows, show, limit, after, before)}


def conversations(s: Session) -> dict[str, Any]:
    """The person's conversations, latest first, with what is unread."""
    pairs: dict[str, dict[str, Any]] = {}
    log = Log(s.read_all(channel=DIRECT, kind="message"))
    for n, e in log.by_number.items():
        other = str(e.fields.get("to", "")) if e.sender == s.name else e.sender
        key = tuple(sorted((s.name, other)))
        p = pairs.setdefault(other, {"a": key[0], "b": key[1], "messages": 0, "last": 0.0, "unread": 0})
        p["messages"] += 1
        p["last"] = e.at_ms / 1000
        p["unread"] += e.sender == other and n > _marks(s, "dm:" + other)
    return {"conversations": sorted(pairs.values(), key=lambda p: p["last"], reverse=True)}


def head(s: Session) -> dict[str, Any]:
    """A number that grows whenever the board gets a message."""
    return {"seq": sum(s.head().values())}


def wait(s: Session, after: int, seconds: float) -> dict[str, Any]:
    """`head`, once it is past ``after`` or ``seconds`` have gone."""
    end = time.monotonic() + seconds
    while True:
        found = head(s)
        if found["seq"] > after or time.monotonic() >= end:
            return found
        time.sleep(POLL_S)


def _reply(s: Session, to: str, reply: int) -> tuple[str, int]:
    """The id of the message numbered ``reply`` on ``to`` and the number of the thread it is in."""
    if not reply:
        return "", 0
    log = Log(s.read_all(conversation=to, kind="message") if not to.startswith("#") else _channel(s, to).entries)
    found = log.by_number.get(reply)
    if found is None:
        raise ValueError(f"message {reply} is not on {to}")
    return found.id, log.thread(found)


def post(s: Session, doc: dict[str, Any]) -> dict[str, Any]:
    """The person's one write: a message to a room, thread or conversation."""
    to, kind = doc["to"], doc.get("type", "note")
    if kind not in KINDS:
        raise ValueError(f"type is one of {', '.join(KINDS)}")
    if to == ANNOUNCE:
        raise client.Denied("denied", "announcements are posted with the workspace announce command")
    if to.startswith("#") and to != GENERAL and not next((r["member"] for r in s.rooms() if r["name"] == to), False):
        s.room("join", to)
    replied, root = _reply(s, to, doc.get("reply_to", 0))
    sent = s.post(to, kind, doc["body"], subject=doc.get("subject", ""), reply_id=replied)
    number = Log(s.read_all(by=s.name, kind="message")).of(str(sent["id"]))
    return {"seq": number, "to": to, "thread": root or number}


def ack(s: Session, doc: dict[str, Any]) -> dict[str, Any]:
    """Mark a room or conversation read through a message the person can see."""
    through = doc["through"]
    name = doc.get("board", doc.get("to"))
    other = "board" not in doc
    log = Log(s.read_all(conversation=name, kind="message") if other else _channel(s, name).entries)
    if through not in log.by_number:
        raise client.Denied("denied", "through must identify a visible message in this conversation")
    s.keep(MARK + ("dm:" + name if other else name), {"through": through})
    return doc


def file(s: Session, handle: str) -> tuple[dict[str, Any], bytes]:
    """A file the person may read: its description and its bytes."""
    found = next((e for e in s.files() if e.fields["file"]["id"] == handle and not e.gone), None)
    if found is None:
        raise client.Denied("denied", f"file {plain.line(handle, 16)} (not available to you)")
    meta = found.fields["file"]
    try:
        return {"id": meta["id"], "name": meta["name"], "size": meta["size"], "type": meta["type"],
                "text": meta.get("text", False)}, filecli._content(s, found)
    except Refused as err:
        raise client.Denied("denied", str(err)) from err

"""The `poolhouse-workspace` commands that run on the node: who you are, messages, agents, spawn, retire."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from typing import Any

from poolhouse import node_launch
from poolhouse.board import credentials, person, person_key, session
from poolhouse.board.client import Client, Conflict, NodeError
from poolhouse.board.session import Entry, Native, Session
from poolhouse.command import flag
from poolhouse.errors import Denied
from poolhouse.log import say, warn
from poolhouse.workspace import (
    agent_display,
    agent_filter,
    attention,
    inbound,
    limits,
    nudge,
    onboard,
    render,
    worktree_lifecycle,
)
from poolhouse.workspace.cli_options import FOR_AGENT, READ
from poolhouse.workspace.modelid import clean_harness, clean_model, safe
from poolhouse.workspace.plain import data_line
from poolhouse.workspace.screen import Refused, fence, refusals

__all__ = ["TABLE", "exiting", "guarded", "runner"]

TYPES = ("task", "status", "handoff", "question", "answer", "claim", "release", "note", "file")
ANNOUNCE_KINDS = ("joined", "milestone", "done", "blocked")
ANNOUNCE = "#announcements"
Handler = Callable[[argparse.Namespace, Session], Any]


def exit_code(err: Exception) -> int:
    """The exit status for a refusal: 2 malformed, 3 not allowed, 4 over a quota, 5 held by another."""
    if isinstance(err, Conflict):
        return 5
    if isinstance(err, NodeError):
        return {"invalid": 2, "quota": 4}.get(err.code, 3)
    if isinstance(err, (Refused, Denied)):
        return 3
    return 2 if isinstance(err, (ValueError, EOFError)) else 3


FAILURES = (NodeError, OSError, Denied, Refused, ValueError, EOFError)
GENERIC = frozenset({"claude", "chatgpt", "qwen", "claude-code", "codex"})
"""The names `--agent` once carried for every session; each is refused in favour of the session's own."""


def check_agent(given: str, environ: dict[str, str] | None = None) -> str:
    """``given``, or an empty string (with one line saying so) when it is a shared harness name and the session has its own."""
    own = (environ if environ is not None else os.environ).get(session.AGENT_ENV, "")
    if own and given and given in GENERIC and given != own:
        warn(f"--agent {given} is not an identity: running as {own}")
        return ""
    return given


def guarded(run: Callable[[argparse.Namespace], int | None]) -> Callable[[argparse.Namespace], int]:
    """``run`` with its refusals turned into one warning (or one JSON object) and an exit status."""
    def wrapped(args: argparse.Namespace) -> int:
        try:
            if hasattr(args, "agent"):
                args.agent = check_agent(args.agent)
            return int(run(args) or 0)
        except FAILURES as err:
            code = exit_code(err)
            if getattr(args, "json", False):
                say(json.dumps({"error": str(err), "kind": type(err).__name__, "code": code}))
            else:
                warn(f"workspace: {err}")
            return code
    return wrapped


def connect(args: argparse.Namespace, *, notification: bool = False) -> Session:
    """The caller's session on the board its directory belongs to. A person at a terminal who keeps a key allows what the
    node refuses for want of a grant."""
    state = node_launch.default_state()
    node = Client(state, autostart=False, timeout=2.0) if notification else Client(
        state, person=person_key.provider(state, acting=person.acting(state)))
    return session.connect(token_file=args.token_file, agent=args.agent, board=args.board, client=node)


def narrowed(s: Session, result: Any, named: str) -> Any:
    """``result`` cut to the records that name the session ``named`` stands for (a name or an unambiguous start of one); as it is when ``named`` is empty."""
    return agent_filter.narrow(result, agent_filter.resolve([a.name for a in s.agents(True)], named)) if named else result


def runner(handler: Handler) -> Callable[[argparse.Namespace], int]:
    """A command that connects, runs ``handler`` and prints what it returns."""
    def run(args: argparse.Namespace) -> int:
        s = connect(args)
        result = handler(args, s)
        if result is not None:
            after_output = getattr(result, "after_output", None)
            result = narrowed(s, result, getattr(args, "for_agent", ""))
            render.show(args, result)
            sys.stdout.flush()
            render.held_note(result)
            if after_output is not None:
                after_output(result)
        return 0
    return guarded(run)


def exiting(handler: Handler) -> Callable[[argparse.Namespace], int]:
    """A command that connects and runs ``handler``, which prints for itself and returns the exit status."""
    return guarded(lambda args: handler(args, connect(args)))


def screened(what: str, *texts: str) -> None:
    """Refuse text that is too long, holds a credential or names a private term."""
    lim = limits.load(limits.root())
    joined = "\n".join(texts)
    if len(joined.encode()) > lim.body_bytes:
        raise Refused(f"{what} is {len(joined.encode())} bytes; the limit is {lim.body_bytes}")
    why = refusals(joined, limits.denylist_path(limits.root()))
    if str(node_launch.default_state() / "client") in joined:
        why = [*why, "it names the token directory"]
    if why:
        raise Refused(f"{what} was not written: {'; '.join(why)}. Remove it and send again.")


def rollup(s: Session, ack: bool) -> str:
    """The newest few unseen announcements as one fenced block, or an empty string."""
    page = s.read(s.cursor("announce"), channel=ANNOUNCE, digest=True)
    fresh = [e for e in page.entries if e.sender != s.name]
    if ack and page.entries:
        s.keep("announce", page.cursor)
    if not fresh:
        return ""
    top = limits.load(limits.root()).announce_rollup
    lines = [f"[{e.ref}] {e.fields.get('type', '')} {e.sender}: {data_line(e.text, 200)}" for e in fresh[-top:]]
    if len(fresh) > top:
        lines.insert(0, f"(+{len(fresh) - top} older announcements; `digest` lists them)")
    return render.block(lines, "announcements")


def unanswered(s: Session) -> list[dict[str, Any]]:
    """Direct questions, tasks and handoffs to this session older than the owner's `owed_after_s` that it never
    answered (it wrote to the sender, or in reply, after the request); one from a retired session it has read is closed."""
    now, older_s = s.clock(), limits.load(limits.root()).owed_after_s
    sent = s.read(channel="#dm", by=s.name).entries
    owed = []
    asked = [e for e in s.read(inbox=True).entries if e.fields.get("type") in nudge.URGENT and e.fields.get("to") == s.name]
    settled = attention.closed(s, asked)
    for e in asked:
        age = now - e.at_ms / 1000
        if age < older_s or e.id in settled:
            continue
        if not any(m.at_ms > e.at_ms and (m.fields.get("to") == e.sender or m.fields.get("reply_id") == e.ref) for m in sent):
            owed.append({"seq": e.ref, "from": e.sender, "type": e.fields.get("type"), "age_s": age,
                         "subject": data_line(e.fields.get("subject") or e.text, 60)})
    return owed


def whoami(args: argparse.Namespace, s: Session) -> Any:
    """Who the token says you are; --model and --harness record what you say you run on."""
    a = s.whoami(clean_model(args.model) if args.model else "", clean_harness(args.harness))
    return {"id": a.name, "parent": a.parent, "board": s.board, "model": safe(a.model) or "unknown", "model_state": a.model_state,
            "harness": a.harness}


def agents(args: argparse.Namespace, s: Session) -> Any:
    """Every live identity with its model and whether it may coordinate."""
    return [{"id": a.name, "role": "agent", "parent": a.parent, "model": safe(a.model), "model_state": a.model_state,
             "harness": a.harness, "last_acted": a.seen_ms / 1000, "retired": a.retired, "idle": a.idle,
             **agent_display.describe(a)}
            for a in s.agents()]


def spawn(args: argparse.Namespace, s: Session) -> Any:
    """Register a subagent's native session as a child of the caller."""
    made = s.register(Native(clean_model(args.model) if args.model else "", clean_harness(args.harness), args.session))
    return {"id": made.name, "parent": made.parent, "board": made.board}


def retire(args: argparse.Namespace, s: Session) -> Any:
    """End the session the caller runs as, and the subagents below it."""
    name = s.name
    done = s.retire()
    credentials.forget(s.client.state, s.board, name)
    return {"id": done["name"], "tokens_revoked": done["tokens_revoked"], "leases_released": done["leases_released"],
            "subagents_retired": done.get("subagents_retired", 0)}


def announced(s: Session, kind: str, text: str) -> dict[str, Any]:
    """Post one terse line of ``kind`` for everyone's roll-up."""
    chars = limits.load(limits.root()).announce_chars
    if kind not in ANNOUNCE_KINDS:
        raise Refused(f"an announcement is one of {', '.join(ANNOUNCE_KINDS)}, not {kind!r}; "
                      f"for anything else send a direct message to the one agent who needs it")
    if not text.strip() or "\n" in text or len(text) > chars:
        raise Refused(f"an announcement is one line of at most {chars} characters ({len(text)} given); put the "
                      f"detail in a note and link it, such as 'done: see note 12'")
    screened("the announcement", text)
    if kind == "done":
        worktree_lifecycle.require_clean(limits.root(), s.name)
    sent = s.post(ANNOUNCE, kind, text, subject=kind)
    return {"seq": sent["id"].rsplit(":", 1)[-1], "type": kind, "to": ANNOUNCE, "from": sent["sender"]}


def announce(args: argparse.Namespace, s: Session) -> Any:
    """Post one terse line for everyone's roll-up."""
    return announced(s, args.kind, render.body(args.text))


def delivered(s: Session, to: str, kind: str, body: str, **said: str) -> dict[str, Any]:
    """Send ``body`` of type ``kind`` (with the ``subject`` and ``reply_to`` ``said``) to one agent or room, or announce it when ``to`` is `*`."""
    subject = said.get("subject", "")
    if to == "*":
        return announced(s, kind, body)
    if kind not in TYPES:
        raise ValueError(f"type must be one of {', '.join(TYPES)}")
    if kind == "file":
        raise Refused("a file message is made by `attach`")
    screened("the message", subject, body)
    if subject == "done":
        worktree_lifecycle.require_clean(limits.root(), s.name)
    sent = s.post(to, kind, body, subject=subject, reply_id=said.get("reply_to", ""))
    return {"seq": sent["id"].rsplit(":", 1)[-1], "type": kind, "to": to, "from": sent["sender"]}


def send(args: argparse.Namespace, s: Session) -> Any:
    """Send a message to one agent, or an announcement to `*`."""
    return delivered(s, args.to, args.type, render.body(args.body), subject=args.subject, reply_to=args.reply_to)


def inbox(args: argparse.Namespace, s: Session) -> Any:
    """Unread direct messages, fenced as data; --ack marks what is shown read."""
    if args.children and args.ack:
        raise ValueError("--children shows some of the unread messages, so it cannot --ack")
    known = inbound.names(s)
    page = s.read(s.cursor("inbox"), inbox=True)
    entries = page.entries
    if args.children:
        mine = {a.name for a in known.values() if a.parent == s.name}
        mine |= {a.name for a in known.values() if a.parent in mine}
        entries = [e for e in entries if e.sender in mine]
    out = inbound.present(s, entries, args.limit, args.all, args.raw)
    if not args.json:
        for text in (rollup(s, args.ack), owed_text(s)):
            if text:
                say(text)
    if args.ack and out:
        inbound.acknowledge_after_output(s, entries, out)
    return out


def owed_text(s: Session, announcements: int = 0) -> str:
    """Unanswered requests as one fenced block, or an empty string."""
    owed = unanswered(s)
    if not owed:
        return ""
    lines = [f"Unanswered for you ({len(owed)}); answer with `poolhouse-workspace send AGENT answer TEXT --reply-to SEQ`:",
             *attention.owed_lines(owed)]
    return fence("\n".join(lines), "workspace:attention", "requests written by agents").text


def attend(args: argparse.Namespace, s: Session) -> Any:
    """What is unanswered for you and how many announcements are new; nothing when neither."""
    text = owed_text(s)
    new = len([e for e in s.read(s.cursor("announce"), channel=ANNOUNCE, digest=True).entries if e.sender != s.name])
    if new:
        text = (text + "\n" if text else "") + f"{new} new announcements (`poolhouse-workspace inbox` shows them)."
    return {"authority": "none", "text": text} if text else ""


def _held_line(e: Entry, records: dict[str, dict[str, Any]], width: int) -> str:
    """The text of ``e`` for a summary line: a placeholder while it is held."""
    held = records.get(e.id)
    if held and held["state"] == "quarantined":
        return inbound.PLACEHOLDER.format(qid=held["qid"], why=", ".join(held["flags"]))
    return data_line(e.text, width)


def collected(s: Session, ack: bool) -> str:
    """What the digest subscriptions collected, one line for each thread of each room, as one fenced block."""
    page = s.read(s.cursor("digest"), digest=True)
    rows = [e for e in page.entries if e.channel != ANNOUNCE]
    if ack and page.entries:
        s.keep("digest", page.cursor)
    records = inbound.hold(s, rows)
    groups: dict[tuple[str, str], list[Entry]] = {}
    for e in rows:
        groups.setdefault((e.channel, str(e.fields.get("thread_id") or e.id)), []).append(e)
    lines = [f"{room} thread {items[0].ref if items[0].id == root else root} {data_line(items[0].fields.get('subject', ''), 60)!r}: "
             f"{len(items)} new, last from {items[-1].sender}: {_held_line(items[-1], records, 100)}"
             for (room, root), items in sorted(groups.items(), key=lambda g: g[1][-1].at_ms)]
    cap = limits.load(limits.root()).digest_lines
    shown = lines[-cap:]
    if len(lines) > cap:
        shown.insert(0, f"({len(lines) - cap} older threads omitted)")
    return render.block(shown, "digest") if shown else ""


def thread_digest(s: Session, root: str) -> str:
    """A thread in a few lines: its first message and the newest replies."""
    rows = s.read_all(thread=root)
    head, replies = rows[0], rows[1:]
    recent, records = replies[-5:], inbound.hold(s, rows[:1] + replies[-5:])
    lines = [f"[{head.ref}] {head.sender}: {_held_line(head, records, 300)}"]
    if len(replies) > len(recent):
        lines.append(f"({len(replies) - len(recent)} earlier replies omitted)")
    lines += [f"[{e.ref}] {e.sender}: {_held_line(e, records, 200)}" for e in recent]
    return render.block(lines, f"thread#{root}")


def digest(args: argparse.Namespace, s: Session) -> Any:
    """What is new: the announcements and what digest subscriptions collected; --thread N one thread; with
    --status, your active subagents, their claims and what you owe."""
    if args.thread:
        return {"authority": "none", "text": thread_digest(s, args.thread)}
    if not args.status:
        text = "\n".join(part for part in (rollup(s, args.ack), collected(s, args.ack)) if part)
        return {"authority": "none", "text": text or "nothing new"}
    return {"authority": "none", "text": render.block(attention.status_lines(s, owed=unanswered(s)), "status")}


def waiting(s: Session) -> dict[str, Any]:
    """What waits unread, as `nudge.Waiting` takes it; nothing is marked read."""
    entries = s.read(s.cursor("inbox"), inbox=True, limit=nudge.PAGE).entries
    return waiting_entries(entries, s.name, s.clock())


def waiting_entries(entries: list[Entry], me: str, now: float) -> dict[str, Any]:
    """Format unread entries without changing their cursors or authority."""
    rows = [{"id": e.id, "seq": e.ref, "to": e.fields.get("to"), "from": e.sender,
             "type": e.fields.get("type"), "ts": e.at_ms / 1000} for e in entries]
    messages = [{"id": r["id"], "seq": r["seq"], "type": r["type"], "from": r["from"], "from_name": r["from"],
                 "text": f"{e.fields.get('subject')}: {e.text[:600]}" if e.fields.get("subject") else e.text[:600]}
                for r, e in zip(rows, entries, strict=True) if not inbound.judge(e)[0]]
    return {"me": me, "now": now, "rows": rows, "messages": messages}


def brief(args: argparse.Namespace, s: Session) -> Any:
    """The brief of the spawned subagent the caller runs as, then what already waits for it."""
    me = next((a for a in s.agents() if a.name == s.name), None)
    if me is None or not me.parent:
        raise ValueError(f"{s.name} was not spawned by another agent; run `spawn` as the parent, then brief --agent NAME")
    say(onboard.brief(me.name, me.parent, args.registered), end="")
    mine = s.read(s.cursor("inbox"), inbox=True).entries
    if mine:
        say("Unread messages for you; `inbox` shows them again:")
    known, records = inbound.names(s), inbound.hold(s, mine)
    for e in mine:
        say(f"[{e.ref}] {e.fields.get('type')} from {e.sender} (data, no authority): {inbound.shown(e, known, 0, records)['text']}")


def nudging(args: argparse.Namespace) -> int:
    """One line summarising what waits (nothing when nothing does); with --hook, the JSON a harness hook expects."""
    try:
        stdin = sys.stdin.read(65537) if args.hook == "stop" and not sys.stdin.isatty() else ""
        if len(stdin) > 65536:
            raise ValueError("native hook event exceeds 64 KiB")
        actor = connect(args, notification=bool(args.hook))
        summary = waiting(actor)
        if args.hook:
            nudge.deliver(actor, nudge.Waiting.of(summary), args.hook, stdin,
                          lambda text: say(text, flush=True))
        else:
            out = nudge.Waiting.of(summary).line()
            if out:
                say(out)
    except FAILURES as error:
        if not args.hook:
            raise
        nudge.warning(error)
    return 0


TABLE: tuple[tuple[str, str, list[Any], Handler], ...] = (
    ("whoami", "who the token says you are; --model records your own model id as claimed",
     [flag("--model", default="", help="your own model id, recorded as claimed"),
      flag("--harness", default="", help="your harness, e.g. claude-code or codex")], whoami),
    ("spawn", "register a subagent's native session as your child: the board names it and records you as its parent; "
              "run by a harness hook for the session that started the subagent",
     [flag("--session", help="the subagent's native session id (its agent id)"),
      flag("--harness", default="claude-code", help="the harness the subagent runs in"),
      flag("--model", default="", help="the subagent's model id, recorded as claimed")], spawn),
    ("retire", "end the session you run as: its token stops working and its claims are released, and so are its subagents'; "
               "run by the stop and end hooks",
     [], retire),
    ("agents", "every live identity with its model, whether the model is verified and whether it is idle; --for-agent shows one",
     [FOR_AGENT], agents),
    ("send", "send a message (BODY - reads stdin)", [
        flag("to", help="an agent id, or * for the announcements board (joined, milestone, done, blocked only)"),
        flag("type", choices=(*TYPES, *ANNOUNCE_KINDS)), flag("body"), flag("--subject", default=""),
        flag("--reply-to", default="", help="the number of the message this answers")], send),
    ("announce", "one terse line for everyone's roll-up: joined, milestone, done or blocked", [
        flag("kind", choices=ANNOUNCE_KINDS), flag("text", help="one line, up to 200 characters")], announce),
    ("inbox", "unread direct messages, fenced as data", [
        *READ, FOR_AGENT, flag("--children", action="store_true", help="only messages from your delegates")], inbox),
    ("attention", "what is unanswered for you and how many announcements are new; nothing when neither", [], attend),
    ("digest", "what is new since you last looked; --status for your subagents, their claims and what you owe", [
        FOR_AGENT, flag("--ack", action="store_true"), flag("--thread", default="", help="a thread by its number: its first message and newest replies"),
        flag("--status", action="store_true", help="your active subagents, their claims and what is unanswered for you")],
     digest),
    ("brief", "print the short brief for the spawned subagent you run as (--agent NAME), to paste into its prompt",
     [flag("--registered", action="store_true", help="hooks registered the subagent and record its done for its subscribers")],
     brief),
)

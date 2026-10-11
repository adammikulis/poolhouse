"""The note commands of `poolhouse-workspace`: decisions, rules, facts and questions with a trust level each."""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from poolhouse.board.session import Note, Session
from poolhouse.command import flag
from poolhouse.errors import Denied
from poolhouse.workspace import inbound, limits, render
from poolhouse.workspace.board_cli import screened
from poolhouse.workspace.screen import Refused, fence
from poolhouse.workspace.standing import record_injection

__all__ = ["KINDS", "TABLE"]

KINDS = ("decision", "rule", "fact", "question")
OUTPUT_BYTES = 65_536


def allowed_command(argv: list[str], allow: Iterable[Iterable[str]]) -> bool:
    """Whether ``argv`` starts with every word of one allow-list entry."""
    return any(entry and argv[:len(entry)] == entry for entry in (list(e) for e in allow))


def held(s: Session, found: list[Note]) -> dict[str, dict[str, Any]]:
    """The node's quarantine record of each note of ``found`` that must be held from this reader, by note id: text with a hard
    injection marker, or a soft one from a sender without good standing. A note the session wrote is never held from it."""
    flagged = []
    for n in found:
        flags, hard, hold = inbound.judge_text(f"{n.title}\n{n.body}", n.author)
        if hold and n.author != s.name:
            flagged.append((n.id, flags))
            if hard:
                record_injection(n.author)
    return s.hold(flagged) if flagged else {}


def shown(n: Note, record: dict[str, Any] | None = None) -> dict[str, Any]:
    """A note as the commands print it: fenced text, its trust and whether anything replaced it; a held one as its
    placeholder, with nothing its author wrote beside it, until a person releases it."""
    status = f"superseded by {n.superseded_by.rsplit(':', 1)[-1]}" if n.superseded_by else "current"
    proof = n.last_verified
    if proof:
        status += f"; last run exit {proof['exit']} by {proof['by']}"
    fenced = fence(f"{n.title}\n{n.body}" if n.title else n.body, f"workspace:note#{n.ref}", f"{n.kind} by {n.author}").text
    quarantined = record is not None and record["state"] == "quarantined"
    text = inbound.PLACEHOLDER.format(qid=record["qid"], why=", ".join(record["flags"])) if quarantined and record else fenced
    return {"id": n.ref, "kind": n.kind, "title": "" if quarantined else n.title, "author": n.author, "trust": n.trust, "stale": n.stale,
            "status": status, "source": "" if quarantined else n.source, "tags": [] if quarantined else n.tags, "ttl_days": n.ttl_days,
            "verify_cmd": "" if quarantined else n.verify_cmd, "superseded_by": n.superseded_by, "binding": False, "text": text}


def write(s: Session, kind: str, title: str, body: str, **extra: Any) -> dict[str, Any]:
    """Write a note (``extra`` may hold ``source``, ``tags``, ``supersedes``, ``verify_cmd`` and ``ttl_days``); the node sets its trust level."""
    screened("the note", title, body, extra.get("source", ""), extra.get("verify_cmd", ""))
    sent = s.note_add(kind, title, body, **extra)
    return shown(s.notes(ref=sent["id"])[0])


def add(args: argparse.Namespace, s: Session) -> Any:
    """Add a note; the node sets its trust level."""
    return write(s, args.kind, args.title, render.body(args.body), source=args.source, tags=[t for t in args.tags.split(",") if t],
                 supersedes=[r.strip() for r in args.supersedes.split(",") if r.strip()],
                 verify_cmd=args.verify_cmd, ttl_days=int(-(-args.ttl_days // 1)))


def search(args: argparse.Namespace, s: Session) -> Any:
    """Notes matching the words."""
    found = s.notes(query=args.query, kind=args.kind, everything=args.all, limit=args.limit)
    records = held(s, found)
    return [shown(n, records.get(n.id)) for n in found]


def get(args: argparse.Namespace, s: Session) -> Any:
    """One note."""
    [note] = s.notes(ref=args.id)
    return shown(note, held(s, [note]).get(note.id))


def verify(args: argparse.Namespace, s: Session) -> Any:
    """Run the note's re-derive command if the owner allow-listed it, and record the result."""
    note = s.notes(ref=args.id)[0]
    record = held(s, [note]).get(note.id)
    if record and record["state"] == "quarantined":
        raise Refused(f"note {args.id} is held in quarantine as {record['qid']}; a person releases it before its command is run")
    argv = shlex.split(note.verify_cmd) if note.verify_cmd else []
    if not argv:
        raise ValueError(f"note {args.id} has no re-derive command")
    lim = limits.load(limits.root())
    if not allowed_command(argv, lim.verify_allow):
        raise Denied("that command is not on the owner's verify_allow list in limits.json")
    env = {k: os.environ[k] for k in ("PATH", "LANG", "TMPDIR") if k in os.environ}
    try:
        done = subprocess.run(argv, cwd=Path(args.cwd), env=env, capture_output=True, timeout=lim.verify_timeout_s, check=False)
        code, out = done.returncode, done.stdout + done.stderr
    except subprocess.TimeoutExpired as late:
        code, out = 124, (late.stdout or b"") + b"[timed out]"
    except OSError as err:
        code, out = 127, str(err).encode()
    return shown(s.note_verify(args.id, code, hashlib.sha256(out[:OUTPUT_BYTES]).hexdigest()))


TABLE = (
    ("notes-add", "add a note; the node sets its trust level", [
        flag("kind", choices=KINDS), flag("title"), flag("body", help="- reads stdin"),
        flag("--source", default=""), flag("--tags", default="", help="comma separated"),
        flag("--supersedes", default="", help="comma separated note numbers"),
        flag("--verify-cmd", default="", help="a command that re-derives the fact"),
        flag("--ttl-days", type=float, default=0.0, help="days until it is stale")], add),
    ("notes-search", "notes matching the words", [
        flag("query"), flag("--kind", choices=KINDS, default=""),
        flag("--all", action="store_true", help="include superseded notes"), flag("--limit", type=int, default=10)],
     search),
    ("notes-get", "one note", [flag("id")], get),
    ("notes-verify", "run the note's allow-listed command and record the result",
     [flag("id"), flag("--cwd", default=".")], verify),
)

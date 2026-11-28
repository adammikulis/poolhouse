"""What a session said and did, read from records that exist already: a Claude Code transcript (one JSON object a line, under
``<claude config>/projects``) or a delegate's record (`poolhouse.delegate_record`). Read only. The transcript's shape is not a promised
interface, so a line that is not understood is passed over."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from poolhouse import delegate_record, home

__all__ = ["TEXT", "Found", "claude_events", "claude_root", "delegate_events", "described", "found"]

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
AGENT_FILE = re.compile(r"agent-([A-Za-z0-9]{4,40})\.jsonl\Z")
TEXT = 12_000
"""Characters of one event's text."""
WINDOW = 1 << 20
"""Bytes one read of a transcript takes: the newest, or what follows a cursor."""
MOST_FILES = 100
"""The sessions listed, and the subagents of those sessions."""
PROMPT_NOTE = "<"
"""A user line that starts with a tag is the harness's own (a notification, a reminder), not something anybody typed."""


def claude_root() -> Path:
    named = os.environ.get("CLAUDE_CONFIG_DIR")
    return (home.expand(named) if named else home.user_home() / ".claude") / "projects"


@dataclass(frozen=True, slots=True)
class Found:
    """One transcript: of a session, or of the subagent ``agent`` of that session."""

    session: str
    agent: str
    path: Path
    changed: float

    @property
    def id(self) -> str:
        return f"subagent-{self.session}-{self.agent}" if self.agent else f"claude-{self.session}"


def found(since: float, root: Path | None = None) -> list[Found]:
    """The sessions and subagents whose transcript changed after ``since``, newest first."""
    out: list[Found] = []
    root = root or claude_root()
    for pattern, sub in (("*/*.jsonl", False), ("*/*/subagents/agent-*.jsonl", True)):
        for path in root.glob(pattern):
            session = path.parent.parent.name if sub else path.stem
            named = AGENT_FILE.match(path.name) if sub else None
            if not UUID.match(session) or (sub and not named):
                continue
            try:
                changed = path.stat().st_mtime
            except OSError:
                continue
            if changed >= since:
                out.append(Found(session, named[1] if named else "", path, changed))
    newest = sorted(out, key=lambda f: -f.changed)
    leads = [each for each in newest if not each.agent][:MOST_FILES]
    listed = {each.session for each in leads}
    subagents = [each for each in newest if each.agent and each.session in listed][:MOST_FILES]
    return sorted([*leads, *subagents], key=lambda f: -f.changed)


def _rows(path: Path, start: int | None) -> tuple[Iterator[tuple[int, dict[str, Any]]], int]:
    """The whole lines of ``path`` from ``start`` (or its newest `WINDOW` bytes), each with its offset, and where the next read begins."""
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            begin = max(0, size - WINDOW) if start is None or start > size else start
            handle.seek(begin)
            data = handle.read(WINDOW)
    except OSError:
        return iter(()), start or 0
    if start is None and begin:   # the first piece of a window is the end of a line
        skip = data.find(b"\n") + 1
        begin, data = begin + skip, data[skip:]
    whole = data.rfind(b"\n") + 1
    if not whole and len(data) >= WINDOW:   # one line longer than a read is passed over
        return iter(()), begin + len(data)

    def lines() -> Iterator[tuple[int, dict[str, Any]]]:
        at = begin
        for line in data[:whole].split(b"\n")[:-1]:
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            if isinstance(row, dict):
                yield at, row
            at += len(line) + 1

    return lines(), begin + whole


def _when(row: Mapping[str, Any]) -> float:
    try:
        return datetime.fromisoformat(str(row.get("timestamp", "")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _said(text: object, limit: int = TEXT) -> str:
    """``text`` printable, with recognized credentials replaced, cut to ``limit``."""
    return delegate_record.cleaned(text or "", limit).strip()


def _about(arguments: object) -> str:
    """One short line on what a tool was run on."""
    if not isinstance(arguments, Mapping):
        return ""
    for key in ("description", "file_path", "pattern", "command", "query", "url", "prompt", "subject"):
        if isinstance(arguments.get(key), str):
            return " ".join(arguments[key].split())[:160]
    return ""


def _blocks(row: Mapping[str, Any]) -> Iterator[dict[str, Any]]:
    """The events one transcript line holds, without ids."""
    kind, message = row.get("type"), row.get("message")
    if kind not in ("user", "assistant") or not isinstance(message, Mapping) or row.get("isMeta") or row.get("isCompactSummary"):
        return
    content = message.get("content")
    parts = [{"type": "text", "text": content}] if isinstance(content, str) else content if isinstance(content, list) else []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") == "text" and (text := _said(part.get("text"))):
            if kind == "assistant":
                yield {"type": "reply", "text": text}
            elif not text.startswith(PROMPT_NOTE):
                yield {"type": "prompt", "text": text, "from": "lead" if row.get("agentId") else "computer"}
        elif kind == "assistant" and part.get("type") == "tool_use":
            yield {"type": "progress", "text": f"{_said(part.get('name'), 80)} {_about(part.get('input'))}".strip()}
        elif kind == "user" and part.get("type") == "tool_result" and part.get("is_error"):
            yield {"type": "progress", "text": "That tool call failed.", "ok": False}


def claude_events(path: Path, after: int | None, *, sidechain: bool = False) -> tuple[list[dict[str, Any]], int]:
    """The events of a transcript from byte ``after`` (the newest without one), oldest first, and the next ``after``. A session's own
    transcript leaves out the lines of its subagents; a subagent's (``sidechain``) is made of them."""
    rows, following = _rows(path, after)
    events = []
    for offset, row in rows:
        if bool(row.get("isSidechain")) != sidechain:
            continue
        events += [{"id": f"t:{offset}:{n}", "at": _when(row), **event} for n, event in enumerate(_blocks(row))]
    return events, following


def described(entry: Found) -> dict[str, str]:
    """A transcript's title, agent name, model and project folder, from its newest lines (and a subagent's own description file)."""
    out = {"title": "", "agent": "", "model": "", "cwd": "", "last_text": ""}
    rows, _ = _rows(entry.path, None)
    for _offset, row in rows:
        if isinstance(row.get("cwd"), str):
            out["cwd"] = row["cwd"]
        if row.get("type") == "ai-title":
            out["title"] = _said(row.get("aiTitle"), 120)
        if row.get("type") == "agent-name":
            out["agent"] = _said(row.get("agentName"), 80)
        message = row.get("message")
        if row.get("type") == "assistant" and isinstance(message, Mapping):
            out["model"] = _said(message.get("model"), 80) or out["model"]
            for event in _blocks(row):
                if event["type"] == "reply":
                    out["last_text"] = event["text"][:300]
    if entry.agent:
        try:
            meta = json.loads(entry.path.with_suffix(".meta.json").read_text("utf-8"))
        except (OSError, ValueError):
            meta = {}
        out["title"] = _said(meta.get("description") or meta.get("agentType"), 120) if isinstance(meta, dict) else ""
        out["agent"] = entry.agent
    return out


def delegate_events(name: str, after: int | None) -> tuple[list[dict[str, Any]], int]:
    """The events of delegate ``name`` from byte ``after`` of its record, and the next ``after``."""
    at, events, more = after or 0, [], True
    while more and len(events) < 2000:
        rows, at, more = delegate_record.read(name, at)
        for number, row in enumerate(rows):
            kind = row.get("kind")
            if kind == "run":
                event = {"type": "prompt", "text": _said(row.get("task")), "from": "lead"}
            elif kind == "text":
                event = {"type": "reply", "text": _said(row.get("text"))}
            elif kind == "call":
                event = {"type": "progress", "text": f"{_said(row.get('tool'), 80)} {_said(row.get('what') or row.get('command'), 160)}".strip(),
                         **({"ok": False} if row.get("denied") else {})}
            elif kind in ("state", "end"):
                event = {"type": "notice", "kind": "delegate", "text": f"{_said(row.get('state'), 40)}: {_said(row.get('detail'), 600)}".rstrip(": ")}
            elif kind == "error":
                event = {"type": "notice", "kind": "delegate", "text": _said(row.get("why"), 600)}
            else:
                continue
            events.append({"id": f"d:{at}:{number}", "at": float(row.get("at") or 0), **event})
    return events[-200:] if after is None else events, at

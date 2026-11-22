"""Which agent harness a process runs under: the harness processes above it, and the ones a session hook recorded.

An agent that drops the marker variables of its harness (`CLAUDECODE`, ...) is still a child of it. The pre-push hook asks
`scripts/hooks/harness-ancestry` whether any ancestor is a harness, by its name or because a session hook recorded that process
as running a session, so the push rules for an agent apply to it however it started git. A process with no harness above it (a
terminal of your own) is never asked anything.

The record is one small file in the person folder (`~/.poolhouse/person`), which the guards keep agents out of. Nothing else
reads it, and an unreadable record only means the names decide.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psutil

from poolhouse import files, home
from poolhouse.lock import only_one

__all__ = ["HARNESS_PREFIXES", "MOST", "VERSION", "ancestors", "bind", "bound", "harness_parent", "on_event", "path",
           "under_harness", "valid_session"]

SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh", "env", "sudo", "login"}
HARNESS_PREFIXES = ("claude", "codex")
"""A process whose name starts with one of these is a harness (`claude`, `claude-code`, `codex`, `codex-cli`)."""
DEPTH = 12
VERSION = 1
MOST = 64
"""The harness processes kept: the newest recorded."""
EVENTS = ("SessionStart", "UserPromptSubmit")
"""The hook events of the main session that record the process they run under."""


def path() -> Path:
    """The file holding the recorded harness processes."""
    return home.state("person", "harness-sessions.json")


def valid_session(value: Any) -> bool:
    """Whether ``value`` is a plausible native session id."""
    return isinstance(value, str) and 0 < len(value) <= 256 and all(32 < ord(c) < 127 for c in value)


def ancestors(pid: int | None = None) -> list[tuple[int, float, str]]:
    """``(pid, create_time, name)`` of each ancestor of ``pid`` (default this process), nearest first."""
    out: list[tuple[int, float, str]] = []
    try:
        process = psutil.Process(os.getpid() if pid is None else pid).parent()
        while process is not None and process.pid > 1 and len(out) < DEPTH:
            out.append((process.pid, process.create_time(), process.name().lower()))
            process = process.parent()
    except (psutil.Error, OSError):
        pass
    return out


def harness_parent() -> tuple[int, float] | None:
    """The nearest ancestor that is not a shell: the process that ran this hook for the harness."""
    for pid, created, name in ancestors():
        if name not in SHELLS:
            return pid, created
    return None


def under_harness(known: set[tuple[int, float]] | frozenset[tuple[int, float]] = frozenset()) -> bool:
    """Whether an ancestor is a known harness process or one a hook recorded for a session (``known``)."""
    return any(name.startswith(HARNESS_PREFIXES) or (pid, created) in known for pid, created, name in ancestors())


def _rows(where: Path) -> list[dict[str, Any]]:
    held = files.read_json(where, {})
    rows = held.get("processes") if isinstance(held, dict) and files.version_of(held) == VERSION else None
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("pid"), int)
            and isinstance(r.get("created"), (int, float))] if isinstance(rows, list) else []


def _running(pid: int, created: float) -> bool:
    try:
        return abs(psutil.Process(pid).create_time() - created) < 1e-3
    except (psutil.Error, OSError):
        return False


def bound(where: Path | None = None) -> set[tuple[int, float]]:
    """The ``(pid, create_time)`` of each harness process a hook recorded; empty when there is no readable record."""
    return {(r["pid"], float(r["created"])) for r in _rows(where or path())}


def bind(session: str, pid: int, created: float, where: Path | None = None) -> None:
    """Record that the process ``pid`` (started at ``created``) runs ``session``; processes that have ended are dropped.

    Raises `poolhouse.lock.Busy` when another hook holds the record for more than a few seconds."""
    where = where or path()
    where.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with only_one(where.with_name(where.name + ".lock"), timeout=5.0, announce=lambda _: None):
        kept = [r for r in _rows(where) if (r["pid"], float(r["created"])) != (pid, created) and _running(r["pid"], r["created"])]
        kept.append({"pid": pid, "created": created, "session": session})
        files.write_json(where, files.versioned({"processes": kept[-MOST:]}, VERSION))


def on_event(event: dict[str, Any], where: Path | None = None) -> bool:
    """Record the harness process the hook of a main session's start or prompt runs under; whether it recorded one.

    A subagent's event, another event and a session id that is not plausible record nothing."""
    parent = harness_parent()
    if (event.get("hook_event_name") not in EVENTS or event.get("agent_id") or not valid_session(event.get("session_id"))
            or parent is None):
        return False
    bind(event["session_id"], parent[0], parent[1], where)
    return True

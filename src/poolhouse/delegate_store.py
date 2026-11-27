"""What a delegate leaves under the state root: its record, its status, its result and its log, one owner-only folder each."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from poolhouse import home
from poolhouse.board.names import valid_name
from poolhouse.files import read_json, write_json

__all__ = ["Delegate", "finished", "folder", "load", "log_file", "names", "result", "save", "save_result", "set_progress", "set_status", "status"]

ENDED = ("done", "partial", "unapplied", "failed", "stopped")
_STATUS = threading.Lock()
"""Held while the status is read and written again: the runner writes progress while a question marks the state from another thread."""


@dataclass(frozen=True, slots=True)
class Delegate:
    """What the lead asked for: ``name`` is the agent's session on ``board``, ``workdir`` the folder it reads (and, with ``edit``, changes)."""

    name: str
    lead: str
    board: str
    task: str
    workdir: str
    model: str
    label: str = ""
    source: str = ""
    edit: bool = False
    max_turns: int | None = None
    native: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    permissions: str = ""
    """The tier the lead chose: `read-only`, `guarded` or `yolo`."""
    sandboxed: bool = True
    """Whether this computer sandboxes it; without a sandbox it works on the folder itself under its tier's rules."""

    def key(self) -> str:
        """The same lead asking the same thing of the same folder, in the same tier, is the same delegate."""
        asked = "\0".join((self.lead, self.label, self.task, self.source, str(self.edit), self.permissions))
        return hashlib.sha256(asked.encode()).hexdigest()[:16]

    def message(self) -> str:
        """What the model is told first: who it is and what is asked. Its system prompt names neither, so every delegate of one lead
        starts with the same prompt and a server's prompt cache holds the part they share."""
        return f"You are {self.name}.\nTask from {self.lead}:\n{self.task}"


def folder(name: str) -> Path:
    """The owner-only folder of delegate ``name``; ValueError for a name no session has."""
    if not valid_name(name):
        raise ValueError(f"{name!r} is not a delegate's name; `poolhouse-delegate status` lists them")
    path = home.state("delegates", name)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def log_file(name: str) -> Path:
    return folder(name) / "log"


def save(record: Delegate) -> None:
    write_json(folder(record.name) / "record.json", asdict(record))


def load(name: str) -> Delegate | None:
    """The record of ``name``, or None when there is none."""
    row = read_json(home.state("delegates", name) / "record.json", None) if valid_name(name) else None
    if not isinstance(row, dict):
        return None
    try:
        return Delegate(**{k: row[k] for k in Delegate.__dataclass_fields__ if k in row})
    except TypeError:
        return None


def names() -> list[str]:
    """Every delegate with a record, oldest first."""
    root = home.state("delegates")
    found = [load(p.name) for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
    return [r.name for r in sorted((r for r in found if r), key=lambda r: r.started)]


def status(name: str) -> dict[str, Any]:
    """What the runner last wrote: ``state`` (starting, loading, working, asking, done, partial, unapplied, failed or stopped), ``detail``,
    ``beat`` and, once a run has said something, its ``turns`` and last ``tool``."""
    found = read_json(home.state("delegates", name) / "status.json", {}) if valid_name(name) else {}
    return found if isinstance(found, dict) else {}


def set_status(name: str, state: str, detail: str = "") -> None:
    with _STATUS:
        progress = {key: value for key, value in status(name).items() if key in ("turns", "tool")}
        write_json(folder(name) / "status.json", {**progress, "state": state, "detail": detail, "beat": time.time()})


def set_progress(name: str, turns: int, tool: str) -> None:
    """The turns a run has taken and the last tool it called; its state stays as it is."""
    with _STATUS:
        write_json(folder(name) / "status.json", {**status(name), "turns": turns, "tool": tool, "beat": time.time()})


def save_result(name: str, row: dict[str, Any]) -> None:
    write_json(folder(name) / "result.json", row)


def result(name: str) -> dict[str, Any]:
    """The final text and what it cost, or ``{}`` while the delegate has none."""
    found = read_json(home.state("delegates", name) / "result.json", {}) if valid_name(name) else {}
    return found if isinstance(found, dict) else {}


def finished(name: str) -> bool:
    return status(name).get("state") in ENDED

"""What a delegate leaves under the state root: its record, its status, its result, its log, its grading record and the record of what it did
(`delegate_record`), one owner-only folder each. Nothing removes a folder: `stop` and the end of a run remove the working copies they made, never
what is recorded."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from poolhouse import delegate_record, home, lock, safetext
from poolhouse.board.names import valid_name
from poolhouse.delegate_record import ENDED
from poolhouse.files import read_json, write_json

__all__ = ["Delegate", "finished", "folder", "graded", "keep", "load", "log_file", "names", "note", "result", "save", "save_result", "set_phase",
           "set_progress", "set_status", "status"]

PROGRESS = ("turns", "tool", "phase", "round", "now", "heard", "tests", "spent", "notices", "told")
"""What a status keeps when its state changes."""
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
    kind: str = ""
    """The kind of task, as the lead named it; empty in a record made before there were kinds."""
    review_rounds: int = 0
    """The self-check rounds its work may take before delivery (`delegate_review`); 0 is none, which is also what an older record holds."""
    commit: str = ""
    """The commit the project was at when it was started; empty for a folder that is no git project."""
    resumes: int = 0
    """The attempts to continue a pass that something outside its task broke off (`delegate_resume`); 0 is none, which an older record holds."""
    branch: str = ""
    """The branch of the worktree it edits, which is then ``workdir`` (`delegate_tree`); empty for one that edits no worktree of its own."""

    def key(self) -> str:
        """The same lead asking the same thing of the same folder, in the same tier, is the same delegate."""
        asked = "\0".join((self.lead, self.label, self.task, self.source, str(self.edit), self.permissions, str(self.review_rounds)))
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
    return [name for name in delegate_record.names() if load(name)]


def status(name: str) -> dict[str, Any]:
    """What the runner last wrote: ``state`` (starting, loading, working, asking, done, partial, unapplied, rejected, failed or stopped),
    ``detail``, ``beat``, ``since`` the state began, the ``phase`` (work, review or fix) and its ``round`` and, once a run has said something, its
    ``turns`` and last ``tool``, what it is doing ``now``, when it was last ``heard``, its last ``tests``, what it ``spent`` and how many
    ``notices`` its lead was given, the last one ``told``."""
    return delegate_record.held(name, "status.json") if valid_name(name) else {}


def set_status(name: str, state: str, detail: str = "") -> None:
    """Mark the state of ``name``; a state or detail that differs from the last is also an event of its record."""
    with _STATUS:
        held = status(name)
        now = time.time()
        changed = (held.get("state"), held.get("detail")) != (state, detail)
        since = held.get("since", now) if held.get("state") == state else now
        progress = {key: value for key, value in held.items() if key in PROGRESS}
        if changed:
            progress["heard"] = now
        write_json(folder(name) / "status.json", safetext.utf8({**progress, "state": state, "detail": detail, "since": since, "beat": now}))
    if changed:
        delegate_record.append(name, "state", state=state, detail=detail)


def note(name: str, **fields: Any) -> None:
    """Set ``fields`` of the status of ``name``; its state stays as it is."""
    with _STATUS:
        write_json(folder(name) / "status.json", safetext.utf8({**status(name), **fields}))


def set_progress(name: str, turns: int, tool: str) -> None:
    """The turns a run has taken and the last tool it called; its state stays as it is."""
    with _STATUS:
        write_json(folder(name) / "status.json", safetext.utf8({**status(name), "turns": turns, "tool": tool, "beat": time.time()}))


def set_phase(name: str, phase: str, round_: int) -> None:
    """The pass that runs now: its phase and, for a review or a fix, its round."""
    with _STATUS:
        held = {key: value for key, value in status(name).items() if key not in ("turns", "tool", "now")}
        write_json(folder(name) / "status.json", safetext.utf8({**held, "phase": phase, "round": round_, "beat": time.time()}))


def graded(name: str) -> dict[str, Any]:
    """The grading record of ``name``: ``run``, what was asked, served and came of it, and ``grades``, one list of entries in one shape for the
    self-check's rounds (``kind`` "self-check") and the grades others gave (``kind`` "grade"). Empty parts for a run that has none."""
    found = read_json(home.state("delegates", name) / "grading.json", None) if valid_name(name) else None
    found = found if isinstance(found, dict) else {}
    run, grades = found.get("run"), found.get("grades")
    return {"run": run if isinstance(run, dict) else {}, "grades": grades if isinstance(grades, list) else []}


def keep(name: str, *, run: dict[str, Any] | None = None, phase: dict[str, Any] | None = None, grade: dict[str, Any] | None = None) -> None:
    """Add to the grading record of ``name``: fields of its ``run``, one ``phase`` of it that has ended, one ``grade`` entry. Entries are only
    ever added; the runner and a grader may write at once."""
    here = folder(name)
    with lock.only_one(here / "grading.lock", timeout=60, announce=lambda _line: None):
        held = graded(name)
        held["run"].update(run or {})
        if phase is not None:
            held["run"]["phases"] = [*held["run"].get("phases", []), phase]
        if grade is not None:
            held["grades"].append(grade)
        write_json(here / "grading.json", safetext.utf8(held))


def save_result(name: str, row: dict[str, Any]) -> None:
    write_json(folder(name) / "result.json", safetext.utf8(row))


def result(name: str) -> dict[str, Any]:
    """The final text and what it cost, or ``{}`` while the delegate has none."""
    found = read_json(home.state("delegates", name) / "result.json", {}) if valid_name(name) else {}
    return found if isinstance(found, dict) else {}


def finished(name: str) -> bool:
    return status(name).get("state") in ENDED

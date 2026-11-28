"""The scheduled jobs of this account as they are kept, read by `poolhouse schedule`, a job's own process and the page.

One folder per job under ``<state>/schedule``, this account's alone: ``job.json`` (what runs, when, who asked, its state and
results), ``log`` (what its steps printed), ``work`` (where they run) and the files copied in when it was scheduled. A stored job
is a command that runs later, so a record or folder another account can write, a link, or another account's file is never read.

A job is ``waiting`` until its process finds the device free between ``at`` and ``latest``, then ``running``; it ends ``done``,
``failed``, ``missed`` (the time passed and it never started) or ``cancelled``. Nothing is deleted when it ends. Who may create
one is `poolhouse.schedule.who`'s; this module only keeps them.
"""

from __future__ import annotations

import re
import secrets
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from poolhouse import caller, home, private_path
from poolhouse.files import read_json, write_json
from poolhouse.lock import held_by, only_one
from poolhouse.platform import private_dir, private_file

__all__ = ["ENDED", "ID", "RUNNER_LOCK", "STATES", "Refused", "alive", "cancel", "create", "folder", "interpreters", "jobs",
           "load", "log_tail", "root", "update", "view", "when"]

STATES = ("waiting", "running", "done", "failed", "missed", "cancelled")
ENDED = ("done", "failed", "missed", "cancelled")
ID = re.compile(r"s-\d{8}-[0-9a-f]{8}")
RUNNER_LOCK = "runner.lock"
"""Held by the one process that waits for and runs a job, so a job never has two and a dead one is seen to be gone."""
LOG_TAIL = 16_000


class Refused(ValueError):
    """A stored job that may not be read or run, with the reason."""


def root() -> Path:
    """Where the jobs are kept: under the state a person owns and an agent must not redirect (`poolhouse.home.guarded`)."""
    return home.guarded("schedule")


def folder(job: str) -> Path:
    """The folder of job ``job``; `Refused` for anything that is not a job's id."""
    if not ID.fullmatch(str(job)):
        raise Refused(f"{str(job)[:60]!r} is not the id of a scheduled job")
    return root() / job


def _private(path: Path) -> None:
    if why := private_path.problem(path):
        raise Refused(f"{path} {why}; a scheduled job is read only from files this account alone can write")


def _locked():
    top = root()
    top.mkdir(parents=True, exist_ok=True)
    private_dir(top)
    return only_one(top / ".lock", timeout=30.0, announce=lambda _line: None)


def _write(where: Path, record: dict[str, Any]) -> None:
    write_json(where / "job.json", record)
    private_file(where / "job.json")


def create(record: dict[str, Any], prepare: Callable[[Path], None] | None = None) -> dict[str, Any]:
    """Keep a new job: ``prepare`` fills its folder (copies of its inputs, its record's own fields) before the record exists, so a
    job is never seen half made. Returns the record with its id."""
    job = f"s-{time.strftime('%Y%m%d')}-{secrets.token_hex(4)}"
    with _locked():
        where = folder(job)
        where.mkdir()
        private_dir(where)
    record.update(id=job, state="waiting", why="", checked=0.0, started=0.0, ended=0.0, results={}, cancel=None,
                  notice={"sent": 0.0, "error": ""})
    record.setdefault("runner", None)
    made = False
    try:
        (where / "work").mkdir(mode=0o700)
        if prepare is not None:
            prepare(where)
        made = True
    finally:
        if not made:
            shutil.rmtree(where, ignore_errors=True)
    with _locked():
        _write(where, record)
    return record


def load(job: str) -> dict[str, Any] | None:
    """The record of ``job``, None when there is none; `Refused` when it may not be trusted."""
    where = folder(job)
    if not (where / "job.json").exists() and not (where / "job.json").is_symlink():
        return None
    for path in (root(), where, where / "job.json"):
        _private(path)
    record = read_json(where / "job.json", None)
    if not isinstance(record, dict) or record.get("id") != job or record.get("state") not in STATES:
        raise Refused(f"the record of {job} is not in the form a scheduled job is kept in")
    return record


def jobs() -> list[dict[str, Any]]:
    """Every job, oldest first. One that may not be trusted is listed as failed with the reason, and no process runs it."""
    top = root()
    out = []
    for where in sorted(top.iterdir()) if top.is_dir() else []:
        if not ID.fullmatch(where.name):
            continue
        try:
            record = load(where.name)
        except (Refused, OSError) as why:
            record = {"id": where.name, "state": "failed", "why": str(why), "refused": True, "at": 0.0, "latest": 0.0, "steps": [],
                      "results": {}, "name": "", "created": 0.0, "by": {}}
        if record is not None:
            out.append(record)
    return sorted(out, key=lambda one: (one.get("created") or 0, one["id"]))


def update(job: str, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Read, ``change`` and write the record of ``job`` with nobody else writing between."""
    with _locked():
        record = load(job)
        if record is None:
            raise Refused(f"no scheduled job {job}")
        change(record)
        _write(folder(job), record)
    return record


def alive(job: str) -> bool:
    """Whether a process holds the job, to wait for it or to run it."""
    return bool(held_by(folder(job) / RUNNER_LOCK))


def interpreters() -> list[str]:
    """The interpreters that jobs which have not ended name: each one's bench steps', and that of the process holding it. A
    runtime that holds one of them is not removed (`poolhouse.runtime_store.collect`)."""
    named = []
    for record in jobs():
        if record["state"] not in ENDED:
            named += [str((record.get(part) or {}).get("python") or "") for part in ("code", "runner")]
    return [one for one in named if one]


def cancel(job: str, by: str) -> dict[str, Any]:
    """Ask for ``job`` to be cancelled, audited. One that waits with no process ends at once; a process that holds it ends it, and
    stops the step it is running. A job that has ended is left as it ended."""
    def ask(record: dict[str, Any]) -> None:
        if record["state"] in ENDED:
            return
        record["cancel"] = {"by": by, "at": time.time()}
        if record["state"] == "waiting" and not alive(job):
            record.update(state="cancelled", why=f"cancelled by {by}", ended=time.time())

    record = update(job, ask)
    caller.record("schedule.cancel", job=job, by=by, state=record["state"])
    return record


def when(at: float) -> str:
    """``at`` in local time, as a person reads it."""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else ""


def log_tail(job: str, most: int = LOG_TAIL) -> str:
    """The last of what the job's steps printed."""
    try:
        with (folder(job) / "log").open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - most))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def view(record: dict[str, Any]) -> dict[str, Any]:
    """What a person is shown of a job: its state, when it starts, why it waits and what each step came to."""
    state = record["state"]
    held = state not in ENDED and alive(record["id"])
    why = str(record.get("why") or "")
    if state not in ENDED and not held:
        why = (why + "; " if why else "") + ("no process holds this job now: it is picked up when Poolhouse is open or a "
                                             "`poolhouse schedule` command runs")
    results = record.get("results") or {}
    unrun = {"state": "not run" if state in ENDED else "queued", "seconds": 0, "why": ""}
    steps = [{"n": step["n"], "kind": step["kind"], "words": step["words"], **unrun, **results.get(str(step["n"]), {})}
             for step in record.get("steps") or []]
    by = record.get("by") or {}
    runner = (record.get("runner") or {}) if held else {}
    return {"id": record["id"], "name": record.get("name", ""), "state": state, "why": why, "held": held,
            "holder": {"pid": runner.get("pid"), "python": runner.get("python", "")} if runner else None,
            "at": record.get("at", 0.0), "latest": record.get("latest", 0.0), "starts": when(record.get("at", 0.0)),
            "latest_start": when(record.get("latest", 0.0)), "started": when(record.get("started", 0.0)),
            "ended": when(record.get("ended", 0.0)), "created": when(record.get("created", 0.0)),
            "by": str(by.get("agent") or by.get("who") or ""), "steps": steps, "cancel": bool(record.get("cancel")),
            "log": str(folder(record["id"]) / "log"), "notice": record.get("notice") or {}}

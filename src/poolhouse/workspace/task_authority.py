"""Live task authority for managing existing project workers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from poolhouse import worktreerules
from poolhouse.board import place
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.board.taskspec import TASK_ID
from poolhouse.errors import Denied
from poolhouse.workspace import localagent as la

__all__ = ["authorize", "runner_of"]

ACTIVE = ("queued", "working", "blocked", "review", "accepted")


def runner_of(base: Path, s: Session, worker: str) -> la.Agent:
    """The one saved local agent of this board that ``worker`` names, by its record name or its session name."""
    found = [row for name in la.names(base) if (row := la.load(base, name)) and row.board == s.board
             and worker in (row.name, row.identity)]
    if len(found) != 1:
        raise Denied("task management requires one existing local worker")
    return found[0]


def authorize(s: Session, base: Path, worker: str, task: str) -> tuple[la.Agent, dict[str, Any]]:
    """The saved worker and the task, after checking the caller's live task authority: the caller is the session that
    started the worker or the creator of a task that names the worker, and both are on the board of the task."""
    if type(task) is not str or not TASK_ID.fullmatch(task):
        raise ValueError("a task ID is required")
    runner = runner_of(base, s, worker)
    if runner.identity == s.name:
        raise Denied("task management requires separate sessions: a worker does not manage itself")
    spec = Tasks(s).get(task)
    known = {a.name: a for a in s.agents()}
    parent = known[runner.identity].parent if runner.identity in known else ""
    named = runner.identity in spec.get("assignees", [])
    if s.name != parent and not (spec["created_by"] == s.name and named):
        raise Denied("only the worker's parent or the creator of a task that names the worker manages it")
    source = la.check_project(runner.project, base)
    if not source or not worktreerules.checkouts(source) or place.resolve(s.client, Path(source)) != s.board:
        raise Denied("the saved worker source must be a checkout of this board's project")
    probe = {"from": s.name, "state": "clear", "type": "task", "to": runner.identity}
    if not la.obeys(s, runner, probe):
        raise Denied("the worker must already take orders from the one that manages it")
    status = la.status_of(base, runner.name)
    if status.get("state") == "working" and status.get("task") and status["task"] != task:
        raise Denied("the worker is executing another task")
    if spec["state"] not in ACTIVE:
        raise Denied("the task is no longer active")
    return runner, spec

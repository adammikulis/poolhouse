"""Parent-owned task worktrees preserve an immutable repository baseline; the checkout is the task's scope on the board."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from poolhouse import worktreerules
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.net import git
from poolhouse.workspace import integration_git as repo, localagent as la

__all__ = ["activate", "binding", "plan", "suffix"]


def suffix(task: str) -> str:
    """The part of a task id that names its checkout and branch."""
    return re.sub(r"[^a-z0-9]+", "-", task.lower()).strip("-")


def _scope(task: dict[str, Any]) -> dict[str, Any] | None:
    scope = task.get("scope")
    return dict(scope) if isinstance(scope, dict) and scope else None


def plan(owner: str, runner: la.Agent, spec: dict[str, Any]) -> dict[str, Any]:
    """The scope ``owner`` reserves for the queued task ``spec`` of ``runner``: a checkout and a committed baseline, checked
    and not yet recorded; the task's own scope when it has one."""
    if spec["state"] != "queued":
        raise Denied("the worker must be eligible for the queued task")
    source = Path(runner.project).resolve(strict=True)
    checkout = worktreerules.checkouts(source)
    if not checkout or checkout[0] != source:
        raise Denied("the configured source must be a Git checkout")
    primary = checkout[1]
    repo.clean(primary)
    baseline = repo.git(primary, "rev-parse", "HEAD")
    development = repo.git(primary, "branch", "--show-current")
    repo.remote_baseline(primary, development, baseline)
    name = suffix(spec["id"])
    target, branch = primary.parent / f"{primary.name}-{name}", f"{owner}/{name}"
    old = _scope(spec)
    if old:
        if old["worker"] != runner.identity or old["source_project"] != str(source):
            raise Denied("the task worktree belongs to another assignment")
        if old.get("state") == "reserved" and not Path(old["project"]).exists():
            if old["owner"] != owner:
                raise Denied("the reserved task must be reassigned by its recorded parent owner")
            return {**old, "baseline_commit": baseline}
        return old
    if why := worktreerules.worktree_refusal(target, source):
        raise Denied(why)
    if target.exists():
        raise Denied("the task worktree path already exists without an assignment")
    return {"task": spec["id"], "worker": runner.identity, "owner": owner, "project": str(target),
            "source_project": str(source), "baseline_commit": baseline, "branch": branch, "state": "reserved"}


def binding(task: dict[str, Any], worker: str) -> dict[str, Any]:
    """The task's existing trusted worktree assignment of ``worker``."""
    scope = _scope(task)
    if not scope or scope.get("worker") != worker or scope.get("state") not in ("reserved", "active"):
        raise Denied("the task has no prepared isolated worktree")
    source, target = Path(scope["source_project"]), Path(scope["project"])
    checkout = worktreerules.checkouts(source)
    if not checkout or checkout[0] != source.resolve():
        raise Denied("the configured task baseline is no longer a Git checkout")
    if scope["state"] == "active":
        checkout = worktreerules.checkouts(target)
        if not (target / ".git").is_file() or not checkout or checkout[0] != target.resolve() \
                or checkout[0] == checkout[1]:
            raise Denied("the active task checkout is missing or no longer isolated")
    return scope


def activate(s: Session, task: dict[str, Any]) -> dict[str, Any]:
    """Materialize the task's reserved checkout when its assigned worker claims it, and hold its claims."""
    scope = binding(task, s.name)
    target, source = Path(scope["project"]), Path(scope["source_project"])
    s.claim("worktree", str(target), ttl_s=86400)
    s.claim("branch", scope["branch"], ttl_s=86400)
    if (target / ".git").is_file():
        checkout, baseline = worktreerules.checkouts(target), worktreerules.checkouts(source)
        if not checkout or not baseline or checkout[0] != target.resolve() \
                or checkout[0] == checkout[1] or checkout[1] != baseline[1]:
            raise Denied("the existing task checkout is not registered to its assigned repository")
        if why := worktreerules.worktree_refusal(target, source, registered=True):
            raise Denied(why)
        if git.run(["branch", "--show-current"], cwd=target).stdout.strip() != scope["branch"]:
            raise Denied("the task checkout branch changed before claiming")
        if not repo.ancestor(target, scope["baseline_commit"], repo.git(target, "rev-parse", "HEAD")):
            raise Denied("the existing task checkout diverged from its assigned baseline")
    else:
        if scope.get("state") != "reserved":
            raise Denied("the active task checkout disappeared; recover its worktree registration")
        if target.exists():
            raise Denied("the task worktree path contains unassigned files")
        if why := worktreerules.worktree_refusal(target, source):
            raise Denied(why)
        primary = worktreerules.checkouts(source)[1]
        development = repo.git(primary, "branch", "--show-current")
        repo.remote_baseline(primary, development, scope["baseline_commit"])
        if repo.git(primary, "rev-parse", "HEAD") != scope["baseline_commit"]:
            raise Denied("development changed after task reservation; refresh the parent resource assignment")
        git.run(["worktree", "add", "-b", scope["branch"], str(target), scope["baseline_commit"]], cwd=source)
    scope = {**scope, "state": "active"}
    Tasks(s).scope(task["id"], scope)
    return scope

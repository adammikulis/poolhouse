"""Exact reviewed source admission for delegated runtime deployment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from poolhouse import authority, board
from poolhouse.runtime_git import COMMIT, DeployError, git
from poolhouse.workspace import landing

GATE = "runtime.deploy-unmerged"


@dataclass(frozen=True, slots=True)
class Approval:
    """The actor, source and independent accept bound to one runtime commit."""

    primary: Path
    commit: str
    board: str
    branch: str
    actor: str


def current_actor(primary: Path, client: board.client.Client | None = None):
    """The authenticated main agent of this exact source project, without starting a node."""
    try:
        return authority.current_agent(cwd=primary, client=client or board.client.Client(autostart=False))
    except (OSError, ValueError):
        raise DeployError("reviewed runtime admission is unavailable for a live lead agent") from None


def reviewed_branch(own: board.session.Session, primary: Path, commit: str) -> str:
    """Find a still-current local branch from the commit's standing independent reviews."""
    found = landing.accepts(own, commit)
    if landing.rejected(own, found):
        raise DeployError("an independent reviewer rejected the runtime commit")
    branches = sorted({row["branch"] for row in found["reviews"] if row.get("verdict") == "accept"})
    for branch in branches:
        if git(primary, "rev-parse", "--verify", "--end-of-options", f"refs/heads/{branch}", check=False) != commit:
            continue
        specific = landing.accepts(own, commit, branch)
        matching = {**specific, "reviews": [row for row in specific["reviews"] if row["branch"] == branch]}
        if not landing.standing(own, specific) and not landing.standing(own, matching):
            return branch
    raise DeployError("the runtime commit has no standing independent approval on a current local branch")


def validate(approval: Approval, primary: Path, commit: str) -> None:
    """Revalidate an exact source decision against its authenticated node."""
    if approval.primary != primary.resolve() or approval.commit != commit or not COMMIT.fullmatch(commit):
        raise DeployError("the runtime approval does not match this exact source commit")
    try:
        client = board.client.Client(autostart=False)
        caller = current_actor(primary, client)
        if approval.board != caller.session.board:
            raise DeployError("the runtime approval belongs to another project")
        if approval.actor != caller.name:
            raise DeployError("the runtime approval belongs to another agent")
        scope = authority.Scope(cwd=primary)
        authority.require("runtime.deploy", f"runtime deploy {commit} from {primary}", scope=scope)
        authority.require(GATE, f"runtime deploy {commit} from {primary} review {approval.branch}", scope=scope)
        if landing.standing(caller.session, landing.accepts(caller.session, commit, approval.branch)):
            raise DeployError("the runtime commit has no standing independent approval")
        if git(primary, "rev-parse", "--verify", "--end-of-options", f"refs/heads/{approval.branch}") != commit:
            raise DeployError("the reviewed runtime branch changed")
    except (board.client.NodeError, OSError, KeyError, TypeError, ValueError) as exc:
        raise DeployError(f"reviewed runtime admission is unavailable: {exc}") from exc


def approve(primary: Path, commit: str, branch: str = "") -> Approval:
    """Return the checked source decision for the caller's exact commit of ``branch``."""
    try:
        caller = current_actor(primary)
        if not branch:
            branch = reviewed_branch(caller.session, primary, commit)
    except (board.client.NodeError, OSError, KeyError, TypeError, ValueError) as exc:
        raise DeployError(f"reviewed runtime admission is unavailable: {exc}") from exc
    approval = Approval(primary.resolve(), commit, caller.session.board, branch, caller.name)
    validate(approval, primary, commit)
    return approval

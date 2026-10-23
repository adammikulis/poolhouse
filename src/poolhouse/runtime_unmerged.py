"""Exact reviewed source admission for delegated runtime deployment."""

from __future__ import annotations

import os
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
    actor = os.environ.get(authority.AGENT_ENV, "")
    if not actor or approval.actor != actor:
        raise DeployError("the runtime approval belongs to another agent")
    try:
        client = board.client.Client(autostart=False)
        board_id = str(client.call("project_resolve", path=str(primary.resolve()))["board"])
        if approval.board != board_id:
            raise DeployError("the runtime approval belongs to another project")
        own = board.session.connect(agent=actor, board=board_id, client=client)
        me = own.whoami()
        if me.name != actor or me.parent or me.retired:
            raise DeployError("reviewed runtime deployment needs a live lead agent")
        authority.require("runtime.deploy", f"runtime deploy {commit} from {primary}")
        authority.require(GATE, f"runtime deploy {commit} from {primary} review {approval.branch}")
        if landing.standing(own, landing.accepts(own, commit, approval.branch)):
            raise DeployError("the runtime commit has no standing independent approval")
        if git(primary, "rev-parse", "--verify", "--end-of-options", f"refs/heads/{approval.branch}") != commit:
            raise DeployError("the reviewed runtime branch changed")
    except (board.client.NodeError, OSError, KeyError, TypeError, ValueError) as exc:
        raise DeployError(f"reviewed runtime admission is unavailable: {exc}") from exc


def approve(primary: Path, commit: str, branch: str = "") -> Approval:
    """Return the checked source decision for the caller's exact commit of ``branch``."""
    try:
        client = board.client.Client(autostart=False)
        board_id = str(client.call("project_resolve", path=str(primary.resolve()))["board"])
        actor = os.environ.get(authority.AGENT_ENV, "")
        if not branch:
            own = board.session.connect(agent=actor, board=board_id, client=client)
            me = own.whoami()
            if not actor or me.name != actor or me.parent or me.retired:
                raise DeployError("reviewed runtime deployment needs a live lead agent")
            branch = reviewed_branch(own, primary, commit)
    except (board.client.NodeError, OSError, KeyError, TypeError, ValueError) as exc:
        raise DeployError(f"reviewed runtime admission is unavailable: {exc}") from exc
    approval = Approval(primary.resolve(), commit, board_id, branch, actor)
    validate(approval, primary, commit)
    return approval

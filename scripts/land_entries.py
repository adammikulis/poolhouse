"""``land run --entries``: branches named with the exact commit they were requested at."""

from __future__ import annotations

import json
from pathlib import Path

import land_git as lg

from poolhouse import protectedwrites
from poolhouse.board import session
from poolhouse.board.client import NodeError
from poolhouse.workspace import landing


def check(root: Path, target: str, branch: str, tip: str) -> None:
    """Raise ValueError unless ``branch`` is at exactly ``tip`` and shares history with the target.

    A branch whose tip moved since the request is never merged: the requester asks again at the new
    commit, which needs a new review. A branch with no common history with the target is refused.
    """
    lg.refuse_protected(branch, target)
    if not lg.exists(root, branch):
        raise ValueError(f"{branch} does not exist")
    now = lg.out(root, "rev-parse", f"{branch}^{{commit}}")
    if now != tip:
        raise ValueError(f"{branch} is at {now[:12]}, not the requested {tip[:12]}; request it again")
    base = f"origin/{target}" if lg.exists(root, f"origin/{target}") else target
    if lg.git(root, "merge-base", base, branch, check=False).returncode:
        raise ValueError(f"{branch} shares no history with {base}")


def guards(root: Path, target: str, branch: str) -> list[str]:
    """The guarded paths ``branch`` changes against ``target``."""
    base = f"origin/{target}" if lg.exists(root, f"origin/{target}") else target
    return protectedwrites.touches_guards(lg.lines(root, "diff", "--name-only", "--no-renames", f"{base}...{branch}"))


def approval(root: Path, target: str, branch: str, tip: str, rid: str = "") -> dict:
    """The authenticated live queue approval of a guarded source at its exact commit."""
    check(root, target, branch, tip)
    try:
        caller = session.connect(cwd=root)
        queue = landing.fold(caller)
        if queue.paused:
            raise ValueError("landing is paused")
        matches = [req for req in queue.requests.values()
                   if req["branch"] == branch and req["sha"] == tip and req["target"] == target
                   and req["status"] in landing.OPEN and (not rid or req["id"] == rid)]
        for req in reversed(matches):
            why = landing.refusal(caller, req) or landing.standing(caller, req)
            if not why:
                return {"id": req["id"], "branch": branch, "sha": tip, "target": target}
        why = why if matches else "no matching live request"
    except NodeError as error:
        why = str(error)
    raise ValueError(f"{branch}@{tip[:12]} needs authenticated independent queue approval: {why}")


def approvals(root: Path, target: str, sources: list[dict], base: str = "") -> list[dict]:
    """The live exact-commit queue approvals for guarded sources."""
    return [approval(root, target, row["branch"], row["sha"], row.get("id", ""))
            for row in sources if guards(root, base or target, row["sha"])]


def load(root: Path, target: str, path: Path) -> list[str]:
    """The branch names of an entries file, a JSON list of ``{"branch": name, "tip": full sha}``."""
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("an entries file is a non-empty JSON list of {branch, tip}")
    names = []
    for entry in entries:
        branch, tip = str(entry.get("branch", "")), str(entry.get("tip", ""))
        check(root, target, branch, tip)
        names.append(branch)
    return names

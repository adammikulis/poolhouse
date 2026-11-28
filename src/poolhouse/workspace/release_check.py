"""Whether an agent may merge into a protected branch (docs/landing.md, "The merge"), decided at once with the reason: the commit
published needs a standing independent land-review accept and a clean checkout; a pull request into the development branch needs
a passed `lead-review` status on its head; and where review tiers apply, what the change holds may ask for an attack verdict.
A process with no agent marker is not asked at all (`poolhouse.caller`).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from poolhouse import board, caller, devbranch, worktreerules
from poolhouse.workspace import landing, merge_review, review_depth

__all__ = ["MERGER", "REVIEW_STATUS", "pull_request", "refusal"]

TIMEOUT_S = 15
REVIEW_STATUS = "lead-review"
"""The commit status that records the review of a pull request's head, and the check the development branch requires."""
MERGER = "POOLHOUSE_MERGER"
"""The subagent whose merge command the Bash guard is judging: the guard itself runs as the session that started it."""
FIELDS = ("baseRefName", "headRefName", "headRefOid", "statusCheckRollup", "body", "author")
UNREAD = (board.client.NodeError, OSError, ValueError, KeyError, TypeError, review_depth.Unreadable)


def _git(where: str, *args: str) -> str:
    done = subprocess.run(["git", "-C", where, *args], capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def pull_request(cwd: str, number: str, *fields: str) -> dict[str, Any]:
    """What `gh pr view` says of the pull request ``number`` of the repository at ``cwd``; OSError or ValueError when it cannot say."""
    try:
        done = subprocess.run(["gh", "pr", "view", number, "--json", ",".join(fields)], cwd=cwd, capture_output=True, text=True,
                              timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise OSError(f"gh did not answer in {TIMEOUT_S} s") from None
    if done.returncode:
        raise OSError((done.stderr.strip() or "gh could not read the pull request").splitlines()[0])
    found = json.loads(done.stdout)
    if not isinstance(found, dict):
        raise ValueError("gh did not describe a pull request")
    return found


def _tiered(where: str, *bases: str) -> bool:
    """`review_depth.tiered` for the branches ``bases`` of origin, as last fetched."""
    return review_depth.tiered(where, *(f"refs/remotes/origin/{base}" for base in bases))


def _session(where: str):
    return board.session.connect(cwd=Path(where), client=board.client.Client(autostart=False))


def _merger(environ: dict[str, str] | None) -> str:
    return (environ or {}).get(MERGER) or os.environ.get(MERGER, "")


def _pull_request(where: str, number: str, commit: str, merger: str) -> str | None:
    """Why the merge of pull request ``number`` at ``commit`` may not run (empty when it may), or None when the rule for the
    checkout's commit judges it: it targets another branch, or it cannot be read in a repository without review tiers."""
    dev = devbranch.development_branch(where)
    try:
        found = pull_request(where, number, *FIELDS)
    except (OSError, ValueError) as error:
        return (f"pull request {number} cannot be read, so its merge cannot be judged ({error})"
                if _tiered(where, dev, "main") else None)
    base, head = str(found.get("baseRefName")), str(found.get("headRefOid"))
    into_development = base == dev and dev not in devbranch.PROTECTED
    if not into_development and not (base == "main" and _tiered(where, base)):
        return None
    if head != commit:
        return f"the head of pull request {number} is {head[:7]}, not {commit[:7]}: a new commit needs a new `scripts/lead-review`"
    if into_development:
        states = [check.get("state") for check in found.get("statusCheckRollup") or [] if isinstance(check, dict)
                  and check.get("__typename") == "StatusContext" and check.get("context") == REVIEW_STATUS]
        if states != ["SUCCESS"]:
            return (f"{commit[:7]} has no passed `{REVIEW_STATUS}` status: the agent that spawned its author reviews it with "
                    "`scripts/lead-review`")
        if not _tiered(where, base):
            return ""
    try:
        why = merge_review.held(_session(where), where, number, found, merger)
    except UNREAD as error:
        return f"the reviews of {head[:7]} could not be judged ({type(error).__name__}: {error})"
    return why if why or into_development else _published(where, head)[0]


def _published(where: str, commit: str) -> tuple[str, str]:
    """Why ``commit`` (or the checkout's HEAD) has no standing accept in a clean checkout, empty when it has; and that commit."""
    head = _git(where, "rev-parse", "--verify", f"{commit or 'HEAD'}^{{commit}}")
    if not head:
        return "git cannot name the commit that would be published", head
    if _git(where, "status", "--porcelain"):
        return f"the checkout has uncommitted changes, so {head[:7]} is not what would be published", head
    try:
        session = _session(where)
        why = landing.standing(session, landing.accepts(session, head))
    except UNREAD as error:
        return f"no land-review for {head[:7]} could be read ({type(error).__name__}); a reviewer's accept is what releases it", head
    return (f"{head[:7]}: {why}" if why else ""), head


def _main_push(where: str, head: str, merger: str) -> str:
    """Why publishing ``head`` to main lacks the attack verdict its changes ask for, empty when it needs none or has one."""
    try:
        reasons = review_depth.attack_reasons(where, "refs/remotes/origin/main", head)
        if not reasons or set(merge_review.attackers(_session(where), head, "", [])) - {merger}:
            return ""
    except UNREAD as error:
        return f"what {head[:7]} would change on main could not be judged ({type(error).__name__}: {error})"
    return (f"{head[:7]} needs an attack review before main takes it ({reasons[0]}; {len(reasons)} such paths): another agent "
            "records one on the promotion pull request with `scripts/lead-review <PR> <SHA> --verdict accept --attack`")


def refusal(cwd: str, environ: dict[str, str] | None = None, commit: str = "", merge: tuple[str, ...] = ()) -> str:
    """Why an agent may not merge into a protected branch from ``cwd``, or an empty string when it may. ``commit`` is the commit a push
    publishes (the pre-push hook names it); without it, the checkout's HEAD is. ``merge`` is the pull request number and head commit
    of a command line that merges exactly that, or `worktreerules.UNNAMED_MERGE` for any other `gh pr merge`
    (`worktreerules.reviewed_merge`)."""
    if not caller.marked(environ):
        return ""
    where = cwd or str(Path.cwd())
    merger = _merger(environ)
    if len(merge) == 2 and (why := _pull_request(where, *merge, merger)) is not None:
        return why
    if merge == worktreerules.UNNAMED_MERGE and _tiered(where, devbranch.development_branch(where), "main"):
        return ("a merge is judged by the pull request and the head it names: run `gh pr merge N --merge --match-head-commit SHA` "
                "(`--delete-branch` may stand beside them) as the whole command, as `scripts/lead-review` prints it")
    why, head = _published(where, commit)
    if why or merge or not _tiered(where, "main"):
        return why
    return _main_push(where, head, merger)

"""The automatic rule for an agent that merges into a protected branch: allowed when the commit it would publish has a standing
independent land-review accept on the board and no reject, and the checkout is clean; refused at once with the reason otherwise.

A pull request into the development branch is judged by its own head commit instead: the `lead-review` commit status that
`scripts/lead-review` sets must have passed on the commit the merge names. The status records the review and is no security
boundary, since every agent uses one GitHub sign-in (docs/landing.md). Any other branch, and a pull request that cannot be read,
keeps the rule above.

Nothing waits for a person. A process with no agent marker is not asked at all (`poolhouse.caller`).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from poolhouse import board, caller, devbranch
from poolhouse.workspace import landing

__all__ = ["REVIEW_STATUS", "answer", "pull_request", "refusal"]

POOLHOUSE_ENTRY_POINT = "reads the reviews on this computer's board before an agent publishes to a protected branch"
TIMEOUT_S = 15
REVIEW_STATUS = "lead-review"
"""The commit status that records the review of a pull request's head, and the check the development branch requires."""


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


def _development(cwd: str, number: str, commit: str) -> str | None:
    """Why the merge of pull request ``number`` at ``commit`` into the development branch may not run (empty when it may), or
    None when it is not such a merge: the pull request cannot be read, or it targets another branch."""
    try:
        found = pull_request(cwd, number, "baseRefName", "headRefOid", "statusCheckRollup")
    except (OSError, ValueError):
        return None
    dev = devbranch.development_branch(cwd)
    if dev in devbranch.PROTECTED or found.get("baseRefName") != dev:
        return None
    head = str(found.get("headRefOid"))
    if head != commit:
        return f"the head of pull request {number} is {head[:7]}, not {commit[:7]}: a new commit needs a new `scripts/lead-review`"
    states = [check.get("state") for check in found.get("statusCheckRollup") or [] if isinstance(check, dict)
              and check.get("__typename") == "StatusContext" and check.get("context") == REVIEW_STATUS]
    if states != ["SUCCESS"]:
        return (f"{commit[:7]} has no passed `{REVIEW_STATUS}` status: the agent that spawned its author reviews it with "
                "`scripts/lead-review`")
    return ""


def refusal(cwd: str, environ: dict[str, str] | None = None, commit: str = "", merge: tuple[str, ...] = ()) -> str:
    """Why an agent may not merge into a protected branch from ``cwd``, or an empty string when it may. ``commit`` is the commit a push
    publishes (the pre-push hook names it); without it, the checkout's HEAD is. ``merge`` is the pull request number and head commit
    of a command line that merges exactly that (`worktreerules.reviewed_merge`)."""
    if not caller.marked(environ):
        return ""
    where = cwd or str(Path.cwd())
    if len(merge) == 2 and (why := _development(where, *merge)) is not None:
        return why
    head = _git(where, "rev-parse", "--verify", f"{commit or 'HEAD'}^{{commit}}")
    if not head:
        return "git cannot name the commit that would be published"
    if _git(where, "status", "--porcelain"):
        return f"the checkout has uncommitted changes, so {head[:7]} is not what would be published"
    try:
        client = board.client.Client(autostart=False)
        session = board.session.connect(cwd=Path(where), client=client)
        found = landing.accepts(session, head)
        why = landing.standing(session, found)
    except (board.client.NodeError, OSError, ValueError, KeyError, TypeError) as exc:
        return f"no land-review for {head[:7]} could be read ({type(exc).__name__}); a reviewer's accept is what releases it"
    return f"{head[:7]}: {why}" if why else ""


def answer(argv: list[str]) -> int:
    """``python -m poolhouse.workspace.release_check commit SHA`` or ``merge [NUMBER SHA]``: what the guards and the pre-push hook run in
    a process of their own, in the checkout they ask about. Prints why an agent may not, nothing when it may; 2 for any other call."""
    if len(argv) == 2 and argv[0] == "commit":
        sys.stdout.write(refusal(".", {"CLAUDECODE": "1"}, argv[1]))
    elif argv[:1] == ["merge"]:
        sys.stdout.write(refusal(".", {"CLAUDECODE": "1"}, merge=tuple(argv[1:])))
    else:
        sys.stderr.write("usage: release_check commit SHA | merge [NUMBER SHA]\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(answer(sys.argv[1:]))

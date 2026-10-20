"""What a stopping subagent leaves unlanded: commits of its own tree that origin's development branch lacks, and uncommitted files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import NamedTuple

import hook_state
import workspace_hook

from poolhouse import devbranch, trees
from poolhouse.gitstate import git

STAGE = "SubagentStop"
REFUSALS = 2


class Held(NamedTuple):
    """One tree that holds work development lacks."""

    branch: str
    path: str
    detail: str


def claimed(environment: dict | None) -> list[dict]:
    """The branch and worktree claims the subagent holds, as the board lists them; empty when the board does not answer."""
    if environment is None:
        return []
    done = workspace_hook.run(["poolhouse-workspace", "claims", "--json"], STAGE, environment=environment)
    try:
        rows = json.loads(done.stdout) if done else []
    except ValueError as error:
        workspace_hook.warning(STAGE, f"claims were not JSON: {error}")
        return []
    owner = environment.get("POOLHOUSE_WORKSPACE_AGENT")
    return [row for row in rows if isinstance(row, dict) and row.get("owner") == owner
            and row.get("kind") in ("branch", "worktree") and isinstance(row.get("key"), str)]


def landed_tip(root: Path, primary: trees.Tree) -> tuple[str, str]:
    """(name, commit) a tree's commits must be in: origin's development branch, else the local one, else the primary's HEAD."""
    name = devbranch.development_branch(root)
    for label, ref in ((f"origin/{name}", f"refs/remotes/origin/{name}"), (name, f"refs/heads/{name}")):
        try:
            return label, git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        except RuntimeError:
            continue
    return "the development branch", primary.head


def holds(tree: trees.Tree, label: str, tip: str) -> Held | None:
    """The unlanded work in ``tree``, or None when it is clean and every commit is in ``tip``."""
    operations, unsaved = trees.dirt(tree.path)
    commits, files = trees.unlanded(tree.path, tip, tree.head), len(operations) + unsaved.total
    parts = []
    if commits:
        parts.append(f"{commits} {'commit' if commits == 1 else 'commits'} not on {label}")
    if files:
        parts.append(f"{files} uncommitted {'path' if files == 1 else 'paths'}")
    return Held(tree.branch or "(detached)", str(tree.path), " and ".join(parts)) if parts else None


def held_trees(cwd: str, agent: str, environment: dict | None) -> list[Held]:
    """The subagent's own trees that hold unlanded work: the one the harness made for it and the ones it claimed.

    The harness names the worktree it makes for a subagent `agent-ID`; a tree the subagent only stopped in is not its own.
    """
    here = Path(cwd)
    if not cwd or not here.is_dir():
        return []
    try:
        top = Path(git(here, "rev-parse", "--show-toplevel")).resolve()
    except (RuntimeError, OSError):
        return []
    try:
        found = trees.listing(top)
        if len(found) < 2:
            return []
        keys = {Path(row["key"]).resolve() if row["kind"] == "worktree" else row["key"] for row in claimed(environment)}
        made = f"agent-{agent}" if agent else ""
        mine = [tree for tree in found[1:] if tree.path in keys or tree.branch in keys or tree.path.name == made]
        if not mine:
            return []
        label, tip = landed_tip(top, found[0])
        return [held for tree in mine if (held := holds(tree, label, tip))]
    except (RuntimeError, OSError, ValueError) as error:
        return [Held("(unreadable)", str(top), f"git could not read the tree: {error}")]


def reason(held: list[Held]) -> str:
    """The sentence that refuses the stop and names the ways out."""
    work = "; ".join(f"{one.branch} in {one.path} holds {one.detail}" for one in held)
    closing = " and ".join(f"scripts/worktrees close {one.path} --bundle" for one in held)
    return (f"Do not stop yet, {work}: land it with scripts/land, or when it is superseded or cannot land run {closing} "
            "(--abandon 'reason' in place of --bundle when files are uncommitted) from outside the tree.")


def where(held: list[Held]) -> str:
    """The trees as an announcement names them: branch and directory name."""
    return ", ".join(f"{one.branch} in {Path(one.path).name}" for one in held)


def counter(agent: str) -> Path:
    """The file that counts the refusals of one subagent."""
    return hook_state.state_dir() / "stop-refusals" / hashlib.sha256(agent.encode()).hexdigest()[:24]


def refused(agent: str) -> int:
    """How many stops of this subagent were refused so far."""
    try:
        return int(counter(agent).read_text())
    except (OSError, ValueError):
        return 0


def remember(agent: str, count: int) -> None:
    """Record the number of refusals; zero forgets them."""
    path = counter(agent)
    if count:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(str(count))
    else:
        path.unlink(missing_ok=True)


def say(kind: str, text: str, environment: dict | None) -> None:
    """Announce as the subagent; without its identity the line goes to the warning stream."""
    if environment is None:
        workspace_hook.warning(STAGE, f"{kind}: {text}")
        return
    workspace_hook.run(["poolhouse-workspace", "announce", kind, text[:200]], STAGE, environment=environment)


class Verdict(NamedTuple):
    """The answer for one stop: the reason to refuse it (empty to let it go), and whether the subagent's own work is unfinished."""

    reason: str
    unfinished: bool


def check(event: dict, environment: dict | None) -> Verdict:
    """Whether this stop is refused and whether the subagent is finished.

    The first two refusals of a subagent are answered; the third stop goes through with a milestone
    naming the tree, and the subagent is not finished, so its identity stays for a resume that lands
    the work. A stop with nothing unlanded forgets the count.
    """
    agent = str(event.get("agent_id") or event.get("cwd") or "")
    try:
        held = held_trees(str(event.get("cwd") or ""), str(event.get("agent_id") or ""), environment)
        used = refused(agent)
        if not held:
            remember(agent, 0)
            return Verdict("", False)
        if used >= REFUSALS:
            remember(agent, 0)
            say("milestone", f"handed back with unlanded work, identity kept: {where(held)}", environment)
            return Verdict("", True)
        remember(agent, used + 1)
    except (OSError, RuntimeError, ValueError) as error:
        workspace_hook.warning(STAGE, f"landed check: {error}")
        return Verdict("", True)
    if used + 1 == REFUSALS:
        say("blocked", f"stop refused twice, work not landed: {where(held)}", environment)
    return Verdict(reason(held), True)

"""``land finish``: fast-forward the target from the verified batch and remove landed branches."""

from __future__ import annotations

import json
import os
from pathlib import Path

import land_entries
import land_git as lg
import land_run

from poolhouse import protectedwrites
from poolhouse.activity.gate import tree_hash


def load(root: Path) -> dict:
    """The last batch's state, or an empty dict."""
    path = land_run.state_file(root)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def inside(path: Path) -> bool:
    """Whether the current directory is ``path`` or below it."""
    here = Path.cwd().resolve()
    return here == path.resolve() or path.resolve() in here.parents


def full_running(state: dict) -> bool:
    """Whether the background full run started for this batch is still alive."""
    full = state.get("full")
    pid = full.get("pid") if isinstance(full, dict) else None
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    return True


def approvals_stand(root: Path, state: dict) -> None:
    """Revalidate guarded source approvals and the integration merge chain."""
    lg.refuse_protected(state["target"], state["integration"])
    actual_base = lg.out(root, "rev-parse", f"{state['target']}^{{commit}}")
    if not land_entries.guards(root, actual_base, state["integration"]):
        return
    if state["base"] != actual_base:
        raise ValueError("the guarded batch base differs from the current target; run land again")
    sources = {row["branch"]: row for row in state.get("source_shas", [])}
    accepted = {row["branch"]: row for row in state.get("approvals", [])}
    previous = state["base"]
    chain = lg.lines(root, "rev-list", "--first-parent", "--reverse", f"{previous}..{state['integration']}")
    merged = state["merged"]
    if len(chain) != len(merged) or len(set(merged)) != len(merged):
        raise ValueError("the guarded integration has an unrecorded merge chain")
    for name, commit in zip(merged, chain, strict=True):
        row = sources.get(name)
        if not row or lg.out(root, "rev-list", "--parents", "-n", "1", commit).split()[1:] != [previous, row["sha"]]:
            raise ValueError("the guarded integration differs from its recorded source commits")
        automatic = lg.git(root, "merge-tree", "--write-tree", previous, row["sha"], check=False)
        expected = automatic.stdout.splitlines()[0] if automatic.stdout else ""
        if not expected or lg.git(root, "cat-file", "-t", expected, check=False).stdout.strip() != "tree":
            raise ValueError("the guarded integration cannot be reconstructed")
        changed = lg.lines(root, "diff", "--name-only", "--no-renames", expected, commit)
        if protectedwrites.touches_guards(changed):
            raise ValueError("the guarded integration changes reviewed merge content")
        land_entries.check(root, state["target"], name, row["sha"])
        if land_entries.guards(root, state["base"], row["sha"]):
            review = accepted.get(name)
            if not review or review.get("sha") != row["sha"] or review.get("target") != state["target"]:
                raise ValueError(f"{name} has no recorded exact-commit approval")
            land_entries.approval(root, state["target"], name, row["sha"], review.get("id", ""))
        previous = commit


def readiness(root: Path, state: dict) -> tuple[str, str]:
    """A reason the batch cannot be fast-forwarded, and the command a lead would run instead."""
    wt, target = Path(state["worktree"]), state["target"]
    top = lg.primary(root)
    command = f"git -C {top.path} merge --ff-only {state['integration']}"
    if state.get("status") != "verified":
        return f"batch status is {state.get('status')}", ""
    if not wt.exists() or lg.dirty(wt) or tree_hash(wt) != state["tree"]:
        return "the integration worktree is missing, dirty or no longer the verified tree", ""
    if lg.git(root, "merge-base", "--is-ancestor", target, state["integration"], check=False).returncode:
        return f"{target} moved since the batch was cut; run land again", ""
    try:
        approvals_stand(root, state)
    except (ValueError, RuntimeError) as error:
        return str(error), ""
    if top.branch != target:
        return f"the primary checkout is on {top.branch or 'a detached head'}, not {target}", command
    if lg.dirty(top.path):
        return "the primary checkout has uncommitted changes", command
    return "", command


def removable(root: Path, upstream: str, name: str) -> str:
    """Why ``name`` must stay (unique patches, a dirty or occupied tree), or an empty string."""
    if lg.unique(root, upstream, name):
        return "unique patches remain"
    tree = lg.tree_of(root, name)
    if tree is None:
        return ""
    if tree.path == lg.primary(root).path:
        return "primary checkout"
    if lg.dirty(tree.path):
        return "dirty worktree"
    if inside(tree.path):
        return "current directory is inside it"
    return ""


def remove(root: Path, name: str) -> str:
    """Remove the worktree and branch of ``name``; an empty string on success, else why not."""
    tree = lg.tree_of(root, name)
    top = lg.primary(root).path
    if tree is not None:
        done = lg.git(top, "worktree", "remove", str(tree.path), check=False)
        if done.returncode:
            return (done.stderr.strip().splitlines() or ["worktree remove failed"])[-1]
    done = lg.git(top, "branch", "-d", name, check=False)
    return "" if done.returncode == 0 else "branch -d refused (not merged by ancestry)"


def cleanup(root: Path, state: dict, apply: bool) -> tuple[list[str], list[dict]]:
    """Remove (or list) the landed branches and the integration branch."""
    gone, kept = [], []
    ejected = {e["branch"] for e in state.get("ejected", [])}
    for name in [n for n in state.get("candidates", state["sources"]) if n not in ejected]:
        problem = removable(root, state["integration"], name)
        problem = problem or (remove(root, name) if apply else "")
        (kept.append({"branch": name, "reason": problem}) if problem else gone.append(name))
    if full_running(state):
        kept.append({"branch": state["integration"], "reason": "background full run in progress"})
    else:
        problem = remove(root, state["integration"]) if apply else ""
        if problem:
            kept.append({"branch": state["integration"], "reason": problem})
    if apply:
        lg.git(root, "worktree", "prune")
    return gone, kept


def finish(root: Path, apply: bool) -> tuple[int, dict]:
    """Fast-forward the target and clean up; the exit status and the summary."""
    state = load(root)
    if not state:
        print("finish: no batch recorded; run `land run` first")
        return 1, {"command": "finish", "status": "none"}
    target = state["target"]
    why, command = readiness(root, state)
    summary: dict = {"command": "finish", "target": target, "apply": apply,
                     "integration": state["integration"]}
    if why:
        print(f"finish: not landing: {why}")
        if command:
            print(f"finish: for the lead: {command}")
        return 3, {**summary, "status": "blocked", "reason": why, "lead_command": command}
    if apply:
        try:
            approvals_stand(root, state)
        except (ValueError, RuntimeError) as error:
            return 3, {**summary, "status": "blocked", "reason": str(error)}
        lg.git(lg.primary(root).path, "merge", "--ff-only", state["integration"])
    print(f"finish: {'fast-forwarded' if apply else 'would fast-forward'} {target} to "
          f"{lg.out(root, 'rev-parse', '--short', state['integration'])}; the push is the lead's")
    gone, kept = cleanup(root, state, apply)
    for name in gone:
        print(f"finish: {'removed' if apply else 'would remove'} {name}")
    for row in kept:
        print(f"finish: kept {row['branch']}: {row['reason']}")
    return 0, {**summary, "status": "landed" if apply else "dry-run", "removed": gone, "kept": kept}

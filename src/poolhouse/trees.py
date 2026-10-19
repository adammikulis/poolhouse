"""Worktrees as git states them: landed (clean, every commit in development) or work (anything else).

A work tree is never removed automatically. ``close`` removes one tree on a decision and ``sweep``
removes the landed trees nobody has touched for ``MIN_AGE_S`` and no process is working in.
"""

from __future__ import annotations

import re
import tarfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import psutil

from poolhouse import gitstate
from poolhouse.gitstate import Unsaved, git

WORKERS = 8
PROTECTED = ("main", "master")
MIN_AGE_S = 600.0
BRANCH_MIN_AGE_S = 86400.0
INTEGRATION = "land/"
CLOSED = {"bundle": "bundled", "abandon": "abandoned"}
MARKERS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
ACTIVITY = ("HEAD", "index", "logs/HEAD")


class Refused(Exception):
    """A removal the tree's state does not allow."""


@dataclass(frozen=True)
class Tree:
    """One entry of ``git worktree list``."""

    path: Path
    branch: str
    head: str
    locked: bool
    stale_lock: bool = False


def dead_owner(reason: str) -> bool:
    """Whether a lock reason names a process (``pid N``) that is no longer running."""
    match = re.search(r"\bpid (\d+)", reason)
    return bool(match) and not psutil.pid_exists(int(match.group(1)))


def listing(root: Path | str) -> list[Tree]:
    """Every registered worktree; the first is the primary checkout."""
    found, fields = [], {}
    for line in [*git(root, "worktree", "list", "--porcelain").splitlines(), ""]:
        key, _, value = line.partition(" ")
        if line:
            fields[key] = value
        elif fields:
            stale = "locked" in fields and dead_owner(fields["locked"])
            found.append(Tree(Path(fields["worktree"]).resolve(), fields.get("branch", "").removeprefix("refs/heads/"),
                              fields.get("HEAD", ""), "locked" in fields and not stale, stale))
            fields = {}
    return found


def dirt(path: Path) -> tuple[list[str], Unsaved]:
    """The unfinished operations and the paths the tree's commit and index do not represent."""
    operations = [m for m in MARKERS if (path / git(path, "rev-parse", "--git-path", m)).exists()]
    return operations, gitstate.unsaved(path)


def removal_content(path: Path) -> str:
    """The tree's HEAD, or Refused when anything in it would be lost by removing it."""
    operations, unsaved = dirt(path)
    if operations:
        raise Refused(f"{path} has an unfinished {operations[0]} operation")
    if unsaved.changed:
        raise Refused(f"{path} has dirty tracked or untracked files; preserve them before cleanup")
    if unsaved.hidden:
        raise Refused(f"{path} has edits its index hides (assume-unchanged or skip-worktree): {unsaved.hidden[:5]}")
    if unsaved.ignored:
        raise Refused(f"{path} contains ignored files requiring preservation: {unsaved.ignored[:5]}")
    return git(path, "rev-parse", "HEAD")


def unlanded(path: Path, tip: str, ref: str) -> int:
    """Commits of ``ref`` that development lacks: patches it does not hold, and merges it does not contain."""
    plus = sum(1 for line in git(path, "cherry", tip, ref).splitlines() if line.startswith("+"))
    return plus + int(git(path, "rev-list", "--merges", "--count", f"{tip}..{ref}"))


def standing(tree: Tree, tip: str, now: float) -> dict:
    """One tree against the development tip: unlanded commits, dirty paths, ahead and behind, age of its newest commit."""
    behind, ahead = (int(x) for x in git(tree.path, "rev-list", "--left-right", "--count", f"{tip}...{tree.head}").split())
    operations, unsaved = dirt(tree.path)
    held, count = unlanded(tree.path, tip, tree.head), len(operations) + unsaved.total
    stamp = int(git(tree.path, "show", "-s", "--format=%ct", tree.head))
    return {"path": str(tree.path), "branch": tree.branch or "(detached)", "head": tree.head, "locked": tree.locked,
            "landed": not held and not count, "unlanded": held, "dirty": count, "ahead": ahead, "behind": behind,
            "tip_age_d": max(0.0, (now - stamp) / 86400)}


def _read(tree: Tree, tip: str, now: float) -> dict:
    """``standing`` of one tree, or a work row saying why git could not be read there."""
    try:
        return standing(tree, tip, now)
    except (RuntimeError, OSError, ValueError) as error:
        return {"path": str(tree.path), "branch": tree.branch or "(detached)", "head": tree.head, "locked": tree.locked,
                "landed": False, "unlanded": 0, "dirty": 0, "ahead": 0, "behind": 0, "tip_age_d": 0.0,
                "unreadable": str(error)}


def rows(root: Path | str, now: float, target: str = "") -> list[dict]:
    """The standing of every tree other than the primary checkout, against ``target`` or the development branch."""
    found = listing(root)
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(lambda tree: _read(tree, target or found[0].head, now),
                             [tree for tree in found[1:] if tree.path.exists()]))


def touched(path: Path) -> float:
    """When the tree's directory, HEAD, index or HEAD log last changed."""
    gitdir = Path(git(path, "rev-parse", "--absolute-git-dir"))
    return max(p.stat().st_mtime for p in (path, *(gitdir / name for name in ACTIVITY)) if p.exists())


def _stem(primary: Path, row: dict) -> Path:
    """The path, in the bundles folder of the repository, that names this tree's saved files; the folder is made."""
    folder = Path(git(primary, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "poolhouse-bundles"
    folder.mkdir(exist_ok=True)
    return folder / (re.sub(r"[^A-Za-z0-9._-]", "_", row["branch"]) + "-" + row["head"][:8])


def bundle(primary: Path, row: dict, tip: str) -> Path:
    """Write the tree's unlanded commits to a bundle and verify that it holds the tip."""
    target = _stem(primary, row).with_suffix(".bundle")
    ref = "refs/poolhouse/bundle/" + row["head"][:12]
    git(primary, "update-ref", ref, row["head"])
    try:
        git(primary, "bundle", "create", str(target), f"{tip}..{ref}")
        git(primary, "bundle", "verify", str(target))
        if row["head"] not in git(primary, "bundle", "list-heads", str(target)):
            raise Refused(f"the bundle {target} does not hold {row['head'][:8]}")
    finally:
        git(primary, "update-ref", "-d", ref)
    return target


def salvage(primary: Path, tree: Path, row: dict) -> list[Path]:
    """Write the tree's uncommitted diff and every changed, untracked, hidden or ignored file to the bundles folder."""
    unsaved = gitstate.unsaved(tree)
    names = [*unsaved.changed, *unsaved.hidden, *unsaved.ignored]
    stem, written = _stem(primary, row), []
    try:
        diff = gitstate.run(tree, "diff", "HEAD", "--binary")
        if diff:
            written.append(stem.with_suffix(".dirty.diff"))
            written[-1].write_text(diff)
        present = [name for name in names if (tree / name).is_symlink() or (tree / name).exists()]
        if present:
            written.append(stem.with_suffix(".dirty.tar"))
            with tarfile.open(written[-1], "w") as archive:
                for name in present:
                    archive.add(tree / name, arcname=name)
    except (OSError, tarfile.TarError, RuntimeError) as error:
        raise Refused(f"{tree} has files that could not be saved before removal: {error}") from error
    return written


def _allowed(row: dict, mode: str, reason: str) -> None:
    """Raise Refused unless ``mode`` may be applied to this tree."""
    if mode == "abandon" and not reason.strip():
        raise Refused("--abandon needs a reason")
    if mode == "abandon" and row["branch"] == "(detached)" and row["unlanded"]:
        raise Refused(f"{row['path']} has unlanded commits on no branch; use --bundle")
    if mode == "landed" and not row["landed"]:
        raise Refused(f"{row['path']} is not landed: {row['unlanded']} unlanded commits, {row['dirty']} dirty files; "
                      "land it, or --bundle / --abandon 'reason'")
    if mode == "bundle" and row["dirty"]:
        raise Refused(f"{row['path']} has {row['dirty']} dirty files a bundle cannot hold; preserve them before cleanup")
    if mode == "bundle" and not row["unlanded"]:
        raise Refused(f"{row['path']} has no unlanded commits to bundle; use --landed")


def close(root: Path | str, path: Path | str, decision: tuple[str, str], keep_branch: str = "") -> dict:
    """Remove one tree on a decision, (mode, reason): ``landed``, ``bundle`` or ``abandon``.

    ``landed`` also deletes the branch, unless ``keep_branch`` gives a reason to keep it. ``bundle`` removes
    the tree after a verified bundle holds its commits. ``abandon`` removes the tree after its uncommitted
    changes are written next to the bundles. Both keep the branch and set its description to
    ``bundled: PATH`` or ``abandoned: REASON``.
    """
    mode, reason = decision
    target, here = Path(path).resolve(), Path.cwd().resolve()
    found = listing(root)
    primary = found[0]
    if target == primary.path or target == here or target in here.parents:
        raise Refused("close runs from outside the tree and never on the primary checkout")
    tree = next((t for t in found[1:] if t.path == target), None)
    if tree is None:
        raise Refused(f"{target} is not a worktree of this repository")
    row = standing(tree, primary.head, 0.0)
    _allowed(row, mode, reason)
    saved = bundle(primary.path, row, primary.head) if mode == "bundle" else ""
    kept = salvage(primary.path, target, row) if mode == "abandon" and row["dirty"] else []
    head = git(target, "rev-parse", "HEAD") if mode == "abandon" else removal_content(target)
    if head != row["head"]:
        raise Refused(f"{target} changed HEAD before cleanup")
    if tree.stale_lock:
        git(primary.path, "worktree", "unlock", str(target))
    git(primary.path, "worktree", "remove", *(["--force"] if kept else []), str(target))
    if target.exists() or any(t.path == target for t in listing(primary.path)):
        raise Refused(f"{target} remains after cleanup")
    done = {"path": str(target), "branch": row["branch"], "head": row["head"], "mode": mode,
            "reason": reason, "bundle": str(saved), "salvage": [str(p) for p in kept]}
    if mode != "landed" and tree.branch:
        git(primary.path, "config", f"branch.{tree.branch}.description", f"{CLOSED[mode]}: {saved or reason.strip()}")
    elif keep_branch:
        done["branch_kept"] = keep_branch
    elif tree.branch and tree.branch not in (*PROTECTED, primary.branch):
        try:
            git(primary.path, "branch", "-d", tree.branch)
        except RuntimeError as error:
            done["branch_kept"] = str(error)
    return done


def created(path: Path, branch: str) -> float:
    """When the branch was created, from its oldest reflog entry; 0.0 when the reflog holds none."""
    stamps = git(path, "reflog", "show", "--format=%ct", f"refs/heads/{branch}").split()
    return float(stamps[-1]) if stamps else 0.0


def occupied(path: Path, directories: list[str | None]) -> bool:
    """Whether any of the working ``directories`` is the tree or inside it; an unreadable one (``None``) may be."""
    return any(d is None or d == str(path) or d.startswith(str(path) + "/") for d in directories)


def sweep(root: Path | str, now: float, working: Callable[[], list[str | None]],
          minimum: float = MIN_AGE_S) -> tuple[list[dict], list[tuple[str, str]]]:
    """Close every landed tree untouched for ``minimum`` seconds; the closed trees and (path, why) for those that stayed.

    ``working`` lists the working directory of every running process. Locked trees, the landing runner's
    integration trees and trees a process is working in are left alone; a tree whose branch is less than
    ``BRANCH_MIN_AGE_S`` old is removed and its branch kept.
    """
    closed, stayed = [], []
    for row in rows(root, now):
        path = Path(row["path"])
        if not row["landed"] or row["locked"] or row["branch"].startswith(INTEGRATION):
            continue
        try:
            if now - touched(path) < minimum or occupied(path, working()):
                continue
            young = row["branch"] != "(detached)" and now - created(path, row["branch"]) < BRANCH_MIN_AGE_S
            closed.append(close(root, path, ("landed", ""), "the branch is less than a day old" if young else ""))
        except (Refused, RuntimeError, OSError) as error:
            stayed.append((row["path"], str(error)))
    return closed, stayed

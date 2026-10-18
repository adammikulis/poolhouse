"""Worktrees as git states them: landed (clean, every commit in development) or work (anything else).

A work tree is never removed automatically. ``close`` removes one tree on a decision and ``sweep``
removes the landed trees nobody has touched for ``MIN_AGE_S``.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

WORKERS = 8
PROTECTED = ("main", "master")
MIN_AGE_S = 600.0
INTEGRATION = "land/"
CLOSED = {"bundle": "bundled", "abandon": "abandoned"}
MARKERS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
ACTIVITY = ("HEAD", "index", "logs/HEAD")
REBUILDABLE = ("dist/*", "build/*", "packaging/build/*", ".build-venv/*", ".build-work/*", "node_modules/*",
               "tests/support/vendor/*.min.js", "__pycache__/*.pyc", "*/__pycache__/*.pyc", ".pytest_cache/*",
               ".mypy_cache/*", ".ruff_cache/*", ".testmondata*", ".coverage", ".coverage.*")
"""Ignored paths, relative to a tree's root, that a build or a test run recreates; no other ignored file may be removed."""


class Refused(Exception):
    """A removal the tree's state does not allow."""


@dataclass(frozen=True)
class Tree:
    """One entry of ``git worktree list``."""

    path: Path
    branch: str
    head: str
    locked: bool


def _run(root: Path | str, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False,
                              timeout=20, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"git {args[0]} did not answer in 20 seconds") from error
    if done.returncode:
        raise RuntimeError(f"git {args[0]} failed (exit {done.returncode}): {done.stderr.strip()[-500:]}")
    return done.stdout


def git(root: Path | str, *args: str) -> str:
    """Stripped standard output of ``git -C root args``; a failure or a stall is a RuntimeError."""
    return _run(root, *args).strip()


def listing(root: Path | str) -> list[Tree]:
    """Every registered worktree; the first is the primary checkout."""
    found, fields = [], {}
    for line in [*git(root, "worktree", "list", "--porcelain").splitlines(), ""]:
        key, _, value = line.partition(" ")
        if line:
            fields[key] = value
        elif fields:
            found.append(Tree(Path(fields["worktree"]).resolve(), fields.get("branch", "").removeprefix("refs/heads/"),
                              fields.get("HEAD", ""), "locked" in fields))
            fields = {}
    return found


def rebuildable(name: str) -> bool:
    """Whether the ignored path ``name`` is one of ``REBUILDABLE``; a nested repository never is."""
    return not name.endswith("/") and ".git" not in Path(name).parts and any(
        fnmatch.fnmatchcase(name, pattern) for pattern in REBUILDABLE)


def dirt(path: Path) -> tuple[list[str], list[str], list[str]]:
    """The unfinished operations, the changed or untracked paths and the ignored paths that are not rebuildable."""
    operations = [m for m in MARKERS if (path / git(path, "rev-parse", "--git-path", m)).exists()]
    changed, ignored = [], []
    entries = iter(_run(path, "status", "--porcelain", "--ignored", "-uall", "-z").split("\0"))
    for entry in entries:
        code, name = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            next(entries, "")
        if code == "!!":
            ignored.extend([] if rebuildable(name) else [name])
        elif entry:
            changed.append(name)
    return operations, changed, ignored


def removal_content(path: Path) -> str:
    """The tree's HEAD, or Refused when anything in it would be lost by removing it."""
    operations, changed, ignored = dirt(path)
    if operations:
        raise Refused(f"{path} has an unfinished {operations[0]} operation")
    if changed:
        raise Refused(f"{path} has dirty tracked or untracked files; preserve them before cleanup")
    if ignored:
        raise Refused(f"{path} contains ignored files requiring preservation: {ignored[:5]}")
    return git(path, "rev-parse", "HEAD")


def unlanded(path: Path, tip: str, ref: str) -> int:
    """Commits of ``ref`` that development lacks: patches it does not hold, and merges it does not contain."""
    plus = sum(1 for line in git(path, "cherry", tip, ref).splitlines() if line.startswith("+"))
    return plus + int(git(path, "rev-list", "--merges", "--count", f"{tip}..{ref}"))


def standing(tree: Tree, tip: str, now: float) -> dict:
    """One tree against the development tip: unlanded commits, dirty paths, ahead and behind, age of its newest commit."""
    behind, ahead = (int(x) for x in git(tree.path, "rev-list", "--left-right", "--count", f"{tip}...{tree.head}").split())
    held, count = unlanded(tree.path, tip, tree.head), sum(len(part) for part in dirt(tree.path))
    stamp = int(git(tree.path, "show", "-s", "--format=%ct", tree.head))
    return {"path": str(tree.path), "branch": tree.branch or "(detached)", "head": tree.head, "locked": tree.locked,
            "landed": not held and not count, "unlanded": held, "dirty": count, "ahead": ahead, "behind": behind,
            "tip_age_d": max(0.0, (now - stamp) / 86400)}


def rows(root: Path | str, now: float, target: str = "") -> list[dict]:
    """The standing of every tree other than the primary checkout, against ``target`` or the development branch."""
    found = listing(root)
    with ThreadPoolExecutor(WORKERS) as pool:
        return list(pool.map(lambda tree: standing(tree, target or found[0].head, now),
                             [tree for tree in found[1:] if tree.path.exists()]))


def touched(path: Path) -> float:
    """When the tree's directory, HEAD, index or HEAD log last changed."""
    gitdir = Path(git(path, "rev-parse", "--absolute-git-dir"))
    return max(p.stat().st_mtime for p in (path, *(gitdir / name for name in ACTIVITY)) if p.exists())


def bundle(primary: Path, row: dict, tip: str) -> Path:
    """Write the tree's unlanded commits to a bundle and verify that it holds the tip."""
    folder = Path(git(primary, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "poolhouse-bundles"
    folder.mkdir(exist_ok=True)
    target = folder / (re.sub(r"[^A-Za-z0-9._-]", "_", row["branch"]) + "-" + row["head"][:8] + ".bundle")
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


def close(root: Path | str, path: Path | str, decision: tuple[str, str]) -> dict:
    """Remove one tree on a decision, (mode, reason): ``landed``, ``bundle`` or ``abandon``.

    ``landed`` also deletes the branch. ``bundle`` removes the tree after a verified bundle holds its
    commits and ``abandon`` removes it; both keep the branch and set its description to ``bundled: PATH``
    or ``abandoned: REASON``.
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
    if removal_content(target) != row["head"]:
        raise Refused(f"{target} changed HEAD before cleanup")
    git(primary.path, "worktree", "remove", str(target))
    if target.exists() or any(t.path == target for t in listing(primary.path)):
        raise Refused(f"{target} remains after cleanup")
    done = {"path": str(target), "branch": row["branch"], "head": row["head"], "mode": mode,
            "reason": reason, "bundle": str(saved)}
    if mode != "landed" and tree.branch:
        git(primary.path, "config", f"branch.{tree.branch}.description", f"{CLOSED[mode]}: {saved or reason.strip()}")
    elif tree.branch and tree.branch not in (*PROTECTED, primary.branch):
        try:
            git(primary.path, "branch", "-d", tree.branch)
        except RuntimeError as error:
            done["branch_kept"] = str(error)
    return done


def sweep(root: Path | str, now: float, minimum: float = MIN_AGE_S) -> tuple[list[dict], list[tuple[str, str]]]:
    """Close every landed tree untouched for ``minimum`` seconds; the closed trees and (path, why) for those that stayed.

    Locked trees and the landing runner's integration trees are left alone.
    """
    closed, stayed = [], []
    for row in rows(root, now):
        if not row["landed"] or row["locked"] or row["branch"].startswith(INTEGRATION):
            continue
        if now - touched(Path(row["path"])) < minimum:
            continue
        try:
            closed.append(close(root, row["path"], ("landed", "")))
        except (Refused, RuntimeError) as error:
            stayed.append((row["path"], str(error)))
    return closed, stayed

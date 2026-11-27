"""The git history of a stage: what lets `git status`, `diff`, `log` and `show` work in the box, in a worktree or not.

A stage is a repository of its own, made by the parent. It is given the commits the project's `HEAD` reaches (the newest `DEPTH`, marked
shallow past them), packed into the stage, on a branch of the same name; the project's own git directory is never granted, so its settings,
hooks, other branches and unreachable objects stay unreadable, and no blob a commit holds under a credential name is packed (a diff of a
commit that changed such a file fails there; its message and the names it changed are still shown). The stage's index
keeps the files of the working copy, so `git diff HEAD` is the project's uncommitted change with the session's, and `git diff` the session's
alone. The files no session is given (`excluded`) stay in the index as `HEAD` has them, marked skip-worktree, so they are not shown as
deleted. The same three views are written under `.git/REVIEW` for a session that reads files and runs no command.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from poolhouse import secretpaths
from poolhouse.activity import source_snapshot

__all__ = ["DEPTH", "REVIEW", "committed", "give"]

DEPTH = 200
"""The commits of `HEAD`'s history a stage holds: enough to read a branch's log and a change's parents, packed in about a second."""
REVIEW = "poolhouse-review"
"""The folder of the stage's git directory that holds `status.txt`, `changes.diff` and `log.txt`, written once at the start."""
VIEWS = (("status.txt", ["status", "--short", "--branch"]), ("changes.diff", ["diff", "--no-ext-diff", "--no-textconv", "HEAD"]),
         ("log.txt", ["log", "-n", "30", "--name-status"]))   # names come from trees: a blob held back is never needed


def _git(where: Path, words: Sequence[str], given: bytes = b"") -> bytes:
    """Git's output for ``words`` in ``where`` with ``given`` on its input, with no configuration of the account's and no hook."""
    done = subprocess.run([str(source_snapshot.actual_git()), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *words],
                          cwd=where, env=source_snapshot.git_environment(), input=given, capture_output=True, timeout=120, check=False)
    if done.returncode:
        raise RuntimeError(f"git {words[0]} failed: {done.stderr.decode(errors='replace').strip()[:300]}")
    return done.stdout


def _under(path: bytes, names: Sequence[bytes]) -> bool:
    return any(path == name or path.startswith(name + b"/") for name in names)


def _commits(source: Path, stage: Path) -> str:
    """Pack the newest commits of the project's `HEAD` into the stage and point a branch of the same name at it; the commit."""
    head = _git(source, ["rev-parse", "--verify", "HEAD"]).decode().strip()
    rows = [line.split() for line in _git(source, ["rev-list", "--parents", f"--max-count={DEPTH}", head]).decode().splitlines()]
    held = {row[0] for row in rows}
    shallow = [row[0] for row in rows if not set(row[1:]) <= held]
    listed = [line.partition(b" ") for line in _git(source, ["rev-list", "--objects", f"--max-count={DEPTH}", head]).split(b"\n") if line]
    named = [name for name, _, path in listed if path and secretpaths.name_hit(os.fsdecode(path))]
    kinds = _git(source, ["cat-file", "--batch-check=%(objectname) %(objecttype)"], b"\n".join(named) + b"\n") if named else b""
    held_back = {row.split()[0] for row in kinds.splitlines() if row.endswith(b" blob")}   # a folder of such a name is still a folder
    wanted = [name for name, _, _ in listed if name not in held_back]
    packs = stage / ".git" / "objects" / "pack"
    packs.mkdir(parents=True, exist_ok=True)
    _git(source, ["pack-objects", "--quiet", str(packs / "history")], b"\n".join(wanted) + b"\n")
    if shallow:
        (stage / ".git" / "shallow").write_text("".join(f"{each}\n" for each in shallow), encoding="ascii")
    try:
        branch = _git(source, ["symbolic-ref", "--quiet", "HEAD"]).decode().strip()
    except RuntimeError:
        branch = ""
    if branch.startswith("refs/heads/"):
        _git(stage, ["update-ref", branch, head])
        _git(stage, ["symbolic-ref", "HEAD", branch])
    else:
        _git(stage, ["update-ref", "--no-deref", "HEAD", head])
    return head


def committed(source: Path) -> bool:
    """Whether the project has a commit, so that there is a history to show."""
    try:
        return bool(_git(source, ["rev-parse", "--verify", "--quiet", "HEAD"]))
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False


def give(source: Path, stage: Path, excluded: Sequence[bytes]) -> str:
    """Give the stage the project's history (see the module). Empty when it did or the project has none, else why git in the box will not
    show it."""
    if not committed(source):
        return ""
    try:
        head = _commits(source, stage)
        kept = [row for row in _git(source, ["ls-tree", "-r", "-z", head]).split(b"\0") if row and _under(row.partition(b"\t")[2], excluded)]
        if kept:
            entries = (b"%s %s\t%s" % (row.split()[0], row.split()[2], row.partition(b"\t")[2]) for row in kept)
            _git(stage, ["update-index", "-z", "--index-info"], b"\0".join(entries) + b"\0")
            _git(stage, ["update-index", "-z", "--skip-worktree", "--stdin"], b"\0".join(row.partition(b"\t")[2] for row in kept) + b"\0")
        views = stage / ".git" / REVIEW
        views.mkdir(exist_ok=True)
        for name, words in VIEWS:
            (views / name).write_bytes(_git(stage, words))
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as why:
        return str(why)
    return ""

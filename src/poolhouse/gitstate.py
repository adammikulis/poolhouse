"""What git holds of a checkout: the index bits that hide edits, and the paths its commit and index do not represent."""

from __future__ import annotations

import fnmatch
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

REBUILDABLE = ("dist/*", "build/*", "packaging/build/*", ".build-venv/*", ".build-work/*", "node_modules/*",
               "tests/support/vendor/*.min.js", "__pycache__/*.pyc", "*/__pycache__/*.pyc", ".pytest_cache/*",
               ".mypy_cache/*", ".ruff_cache/*", ".testmondata*", ".coverage", ".coverage.*")
"""Ignored paths, relative to a checkout's root, that a build or a test run recreates; no other ignored file may be removed."""


def run(root: Path | str, *args: str) -> str:
    """Standard output of ``git -C root args``; a failure or a stall is a RuntimeError."""
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
    return run(root, *args).strip()


def rebuildable(name: str) -> bool:
    """Whether the ignored path ``name`` is one of ``REBUILDABLE``; a nested repository never is."""
    return not name.endswith("/") and ".git" not in Path(name).parts and any(
        fnmatch.fnmatchcase(name, pattern) for pattern in REBUILDABLE)


def _sparse(root: Path) -> bool:
    """Whether the checkout is a sparse checkout."""
    try:
        return run(root, "config", "--type=bool", "--get", "core.sparseCheckout").strip() == "true"
    except RuntimeError:
        return False


def hidden(root: Path) -> list[str]:
    """Tracked paths whose index entry makes git skip the file: assume-unchanged, skip-worktree or fsmonitor-valid.

    In a sparse checkout, a skip-worktree entry whose file is absent is represented by the commit and is not listed.
    """
    sparse, names = _sparse(root), []
    for entry in run(root, "ls-files", "-v", "-f", "-z").split("\0"):
        tag, name = entry[:1], entry[2:]
        if tag.islower() or (tag == "S" and not (sparse and not os.path.lexists(Path(root) / name))):
            names.append(name)
    return names


@dataclass(frozen=True)
class Unsaved:
    """The paths of a checkout that its commit and index do not represent."""

    changed: list[str]
    ignored: list[str]
    hidden: list[str]

    @property
    def total(self) -> int:
        """How many paths there are."""
        return len(self.changed) + len(self.ignored) + len(self.hidden)

    def __bool__(self) -> bool:
        return bool(self.total)


def unsaved(root: Path) -> Unsaved:
    """Changed or untracked paths, ignored paths that are not rebuildable, and paths whose index entry hides edits."""
    changed, ignored = [], []
    entries = iter(run(root, "status", "--porcelain", "--ignored", "-uall", "-z", "--ignore-submodules=none").split("\0"))
    for entry in entries:
        code, name = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            next(entries, "")
        if code == "!!":
            ignored.extend([] if rebuildable(name) else [name])
        elif entry:
            changed.append(name)
    return Unsaved(changed, ignored, [name for name in hidden(root) if name not in changed])

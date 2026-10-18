"""Git queries and worktree helpers for the lander."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROTECTED = ("main", "master")
REDIRECTS = re.compile(
    r"^GIT_(DIR|WORK_TREE|COMMON_DIR|INDEX_FILE|OBJECT_DIRECTORY|ALTERNATE_OBJECT_DIRECTORIES|NAMESPACE|PREFIX|REPLACE_REF_BASE"
    r"|SHALLOW_FILE|CONFIG_COUNT|CONFIG_KEY_\d+|CONFIG_VALUE_\d+|CONFIG_PARAMETERS)$")
STORED = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": os.devnull}


@dataclass(frozen=True)
class Tree:
    """A worktree: its path, the branch checked out there (empty when detached) and its head."""

    path: Path
    branch: str
    head: str


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of a git command: this process's, without the variables that move the repository or add configuration,
    with ``extra`` and with replace refs and the grafts file off."""
    return {**{k: v for k, v in os.environ.items() if not REDIRECTS.match(k)}, **(extra or {}), **STORED}


def git(root: Path | str, *args: str, check: bool = True, env: dict[str, str] | None = None,
        stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run ``git -C root args`` and return the completed process; ``env`` replaces the environment of :func:`clean_env`."""
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          check=False, env=clean_env() if env is None else {**env, **STORED}, input=stdin)
    if check and done.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()}")
    return done


def out(root: Path | str, *args: str) -> str:
    """Stripped standard output of a git command that must succeed."""
    return git(root, *args).stdout.strip()


def empty_tree(root: Path | str) -> str:
    """The id of the tree with no files, in the hash of this repository."""
    return git(root, "hash-object", "-t", "tree", "--stdin", stdin="").stdout.strip()


def lines(root: Path | str, *args: str) -> list[str]:
    """Non-empty output lines of a git command that must succeed."""
    return [x for x in git(root, *args).stdout.splitlines() if x]


def toplevel(start: Path | str) -> Path:
    """The root of the worktree containing ``start``."""
    return Path(out(start, "rev-parse", "--show-toplevel"))


def common_dir(root: Path | str) -> Path:
    """The git directory every worktree of this repository shares."""
    return Path(out(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))


def trees(root: Path | str) -> list[Tree]:
    """Every registered worktree; the first is the primary checkout."""
    found: list[Tree] = []
    path, head, branch = "", "", ""
    for line in [*git(root, "worktree", "list", "--porcelain").stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            path = line[9:]
        elif line.startswith("HEAD "):
            head = line[5:]
        elif line.startswith("branch refs/heads/"):
            branch = line[18:]
        elif not line and path:
            found.append(Tree(Path(path), branch, head))
            path, head, branch = "", "", ""
    return found


def primary(root: Path | str) -> Tree:
    """The primary checkout."""
    return trees(root)[0]


def tree_of(root: Path | str, branch: str) -> Tree | None:
    """The worktree that has ``branch`` checked out, if any."""
    return next((t for t in trees(root) if t.branch == branch), None)


def dirty(path: Path | str) -> bool:
    """Whether the worktree at ``path`` has uncommitted or untracked files; a missing directory has none."""
    if not Path(path).is_dir():
        return False
    return bool(git(path, "status", "--porcelain").stdout.strip())


def exists(root: Path | str, ref: str) -> bool:
    """Whether ``ref`` names a commit."""
    return git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).returncode == 0


def is_ancestor(root: Path | str, older: str, newer: str) -> bool:
    """Whether commit ``older`` is ``newer`` or reachable from it."""
    return git(root, "merge-base", "--is-ancestor", older, newer, check=False).returncode == 0


def refuse_protected(*names: str) -> None:
    """Raise when a branch named here is one landing never touches."""
    for name in names:
        if name in PROTECTED:
            raise ValueError(f"land never touches {name}")

"""Runtime source publication and epoch admission."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from poolhouse import person, worktreerules

FLOOR = "packaging/runtime-floor"
COMMIT = re.compile(r"[0-9a-f]{40}")


class DeployError(RuntimeError):
    """A runtime source, build or selection step failed."""


def git(checkout: Path, *words: str, check: bool = True) -> str:
    """Run git in a checkout and return stdout."""
    done = subprocess.run(["git", "-C", str(checkout), *words], capture_output=True, text=True, timeout=60, env=worktreerules._git_environment())
    if check and done.returncode:
        raise DeployError(f"git {words[0]} failed: {(done.stderr or done.stdout).strip()[:300]}")
    return done.stdout.strip() if done.returncode == 0 else ""


def resolve_commit(checkout: Path, ref: str = "HEAD") -> str:
    """The full commit a ref names in a checkout."""
    if ref.startswith("-"):
        raise DeployError(f"{ref!r} is not a ref")
    commit = git(checkout, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not COMMIT.fullmatch(commit):
        raise DeployError(f"{ref} does not name a commit")
    return commit


def admit(checkout: Path, commit: str, *, allow_unmerged: bool = False) -> None:
    """Require a commit published on the primary development branch, or the terminal person's explicit exception."""
    found = worktreerules.checkouts(checkout)
    if found is None or found[0] != found[1]:
        raise DeployError("runtime selection must use the primary source checkout")
    branch = git(checkout, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    expected = os.environ.get("POOLHOUSE_DEV_BRANCH", "").strip()
    if (not branch or branch in {"main", "master"} or branch.startswith(("land/", "promote/"))
            or (expected and branch != expected)):
        raise DeployError("the primary checkout must be on its development branch")
    if allow_unmerged:
        if person.marked() or not person.is_terminal(sys.stdin) or not person.is_terminal(sys.stdout):
            raise DeployError("only a person at a terminal may deploy an unmerged commit")
        return
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        published = resolve_commit(checkout, ref)
        if git(checkout, "merge-base", commit, published, check=False) != commit:
            raise DeployError(f"{commit[:7]} is not on the development branch published at origin/{branch}")


def parse_epoch(text: str) -> int:
    """The integer in a runtime-floor file; 0 for an empty file, DeployError for anything else."""
    text = text.strip()
    if text and not text.isdigit():
        raise DeployError(f"{FLOOR} must hold one integer, not {text[:40]!r}")
    return int(text or 0)


def floor_of(checkout: Path, commit: str) -> int:
    """The lowest runtime epoch the source at `commit` accepts."""
    return parse_epoch(git(checkout, "show", f"{commit}:{FLOOR}", check=False))



"""Runtime source publication and epoch admission."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from poolhouse import person, runtime_git, runtime_unmerged
from poolhouse.runtime_git import COMMIT as COMMIT, DeployError as DeployError, git as git

FLOOR = "packaging/runtime-floor"


def resolve_commit(checkout: Path, ref: str = "HEAD") -> str:
    """The full commit a ref names in a checkout."""
    if ref.startswith("-"):
        raise DeployError(f"{ref!r} is not a ref")
    commit = git(checkout, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not COMMIT.fullmatch(commit):
        raise DeployError(f"{ref} does not name a commit")
    return commit


def admit(checkout: Path, commit: str, *, allow_unmerged: bool | runtime_unmerged.Approval = False) -> None:
    """Require published source or exact reviewed deployment authority."""
    runtime_git.primary(checkout)
    branch = git(checkout, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    expected = os.environ.get("POOLHOUSE_DEV_BRANCH", "").strip()
    if (not branch or branch in {"main", "master"} or branch.startswith(("land/", "promote/"))
            or (expected and branch != expected)):
        raise DeployError("the primary checkout must be on its development branch")
    if allow_unmerged:
        if person.marked():
            if not isinstance(allow_unmerged, runtime_unmerged.Approval):
                raise DeployError("an agent needs an exact reviewed runtime approval; a person at a terminal may confirm directly")
            runtime_unmerged.validate(allow_unmerged, checkout, commit)
        elif not person.is_terminal(sys.stdin) or not person.is_terminal(sys.stdout):
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



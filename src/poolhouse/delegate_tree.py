"""The worktree and branch of a delegate that edits a git project, which the launcher makes beside it from the commit it is at, so that the lead's
checkout is never written.

What the run changed, and its self-check accepted where one was asked for, is committed there by name; the branch is left for the session that started
the delegate to review, grade and land. Anything else is kept as a patch. The worktree is removed either way, and so is a branch that got no commit.
Nothing is merged or pushed, no hook of the repository runs and no git configuration is written.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from poolhouse import delegate_candidate, delegate_store as store, secretpaths

__all__ = ["BRANCHES", "changed", "committed", "made", "possible", "removed", "settled", "uncommitted"]

BRANCHES = "delegate/"
"""What the name of every branch made here starts with."""
IDENTITY = "poolhouse.invalid"


def _head(source: str) -> str:
    try:
        return delegate_candidate.git(source, "rev-parse", "--verify", "HEAD").decode().strip()
    except (OSError, ValueError):
        return ""


def possible(source: str) -> bool:
    """Whether ``source`` is the root of a git project with a commit to start a branch from."""
    return bool(source) and os.path.lexists(Path(source) / ".git") and bool(_head(source))


def uncommitted(source: str) -> list[str]:
    """The files of ``source`` that differ from its commit, which a worktree made from that commit does not hold."""
    return changed(source)


def changed(folder: str) -> list[str]:
    """The files of the checkout ``folder`` that differ from its commit, new ones included; no credential name."""
    rows = delegate_candidate.git(folder, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames").split(b"\0")
    return [name for name in (os.fsdecode(row[3:]) for row in rows if len(row) > 3) if not secretpaths.name_hit(name)]


def made(source: str, name: str) -> tuple[str, str]:
    """A new worktree of ``source`` beside it, on a new branch for delegate ``name`` at the project's commit: ``(folder, branch)``."""
    root = Path(source)
    folder, branch = root.parent / f"{root.name}-{name}", f"{BRANCHES}{name}"
    delegate_candidate.git(source, "worktree", "add", "--quiet", "--no-track", "-b", branch, str(folder), "HEAD")
    return str(folder), branch


def removed(source: str, folder: str, branch: str, *, keep_branch: bool) -> None:
    """Remove the worktree ``folder`` and, unless ``keep_branch``, the branch; what is gone already stays gone."""
    with contextlib.suppress(OSError, ValueError):
        delegate_candidate.git(source, "worktree", "remove", "--force", folder)
    shutil.rmtree(folder, ignore_errors=True)
    if not keep_branch:
        with contextlib.suppress(OSError, ValueError):
            delegate_candidate.git(source, "branch", "--quiet", "-D", branch)


def committed(folder: str, rec: store.Delegate, paths: Sequence[str]) -> tuple[str, list[str]]:
    """Commit ``paths``, each by name, in the worktree ``folder`` as the delegate: the commit (empty when none of them differs) and the paths git
    would not take. No hook of the repository runs and nothing is signed. ValueError when git cannot commit."""
    left = []
    for path in paths:
        try:
            delegate_candidate.git(folder, "--literal-pathspecs", "add", "--", path)
        except ValueError:
            left.append(path)
    if not delegate_candidate.git(folder, "diff", "--cached", "--name-only", "-z"):
        return "", left
    who = {f"GIT_{role}_{field}": value for role in ("AUTHOR", "COMMITTER")
           for field, value in (("NAME", rec.name), ("EMAIL", f"{rec.name}@{IDENTITY}"))}
    subject = " ".join(rec.task.split())[:60]
    message = f"delegate {rec.name}: {subject}\n\nWork of a local coding agent for {rec.lead}, not yet reviewed by it."
    delegate_candidate.git(folder, "-c", "commit.gpgSign=false", "commit", "--quiet", "--no-verify", "-m", message, env=who)
    return _head(folder), left


def _patched(rec: store.Delegate) -> str:
    """A patch of what the worktree holds that was not committed, kept in the delegate's folder; empty when it holds nothing."""
    names = changed(rec.workdir)
    if not names:
        return ""
    delegate_candidate.git(rec.workdir, "--literal-pathspecs", "add", "--intent-to-add", "--", *names)
    patch = store.folder(rec.name) / "unaccepted.patch"
    patch.write_bytes(delegate_candidate.git(rec.workdir, "--literal-pathspecs", "diff", "--binary", "HEAD", "--", *names))
    return str(patch)


def settled(rec: store.Delegate, state: str, applied: Sequence[str]) -> tuple[str, str, dict[str, str]]:
    """What becomes of the worktree of a run that ended in ``state`` having published ``applied``: ``(state, what the lead is told, the branch,
    commit and patch kept)``. A run `done` or `partial` is committed; one that cannot be ends `unapplied` with a patch."""
    kept, why = {"branch": "", "commit": "", "patch": ""}, ""
    if not Path(rec.workdir).is_dir():
        return state, "", kept
    try:
        if state in ("done", "partial"):
            paths = list(applied) if rec.sandboxed else changed(rec.workdir)
            sha, left = committed(rec.workdir, rec, paths) if paths else ("", [])
            if sha:
                removed(rec.source, rec.workdir, rec.branch, keep_branch=True)
                skipped = f"; {len(left)} file(s) git would not take are not in it" if left else ""
                said = (f"a review is waiting for you: branch {rec.branch} at {sha}{skipped}. Review it, grade it (`poolhouse-delegate grade "
                        f"{rec.name} ...`) and land it yourself: nothing was merged or pushed, and the worktree is removed")
                return state, said, {**kept, "branch": rec.branch, "commit": sha}
        if not rec.sandboxed:
            kept["patch"] = _patched(rec)
    except (OSError, ValueError) as error:
        why = f"; it could not be committed: {error}"
        state = "unapplied" if state in ("done", "partial") else state
        with contextlib.suppress(OSError, ValueError):
            kept["patch"] = _patched(rec)
    removed(rec.source, rec.workdir, rec.branch, keep_branch=False)
    patch = f"; what it changed is kept at {kept['patch']}" if kept["patch"] else ""
    return state, f"nothing was committed, and its worktree and branch are removed{why}{patch}", kept

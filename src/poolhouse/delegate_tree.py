"""The worktree and branch of a delegate that edits a git project, which the launcher makes beside it from the commit it is at, so that the lead's
checkout is never written.

What the run changed is committed there by name, however it ended: a run that was done, or one that failed, was stopped or whose self-check did not
accept it, whose commit says it is unfinished. The commit's words are the launcher's and the task; none are the delegate's. The branch is left for
the session that started the delegate to review, grade and land. The worktree is removed once what it held is committed, and so is a branch still
at the commit it was made at; a worktree with work git could not commit is left as it is. Nothing is merged or pushed, no hook of the repository
runs and no git configuration is written.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from poolhouse import delegate_candidate, delegate_store as store, secretpaths
from poolhouse.isolation.history import IDENTITY

__all__ = ["BRANCHES", "changed", "committed", "made", "possible", "removed", "settled", "uncommitted"]

BRANCHES = "delegate/"
"""What the name of every branch made here starts with."""
FINISHED = ("done", "partial")
"""The states of a run whose commit says only that it is not yet reviewed."""


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


def made(source: str, name: str, commit: str) -> tuple[str, str]:
    """A new worktree of ``source`` beside it, on a new branch for delegate ``name`` at ``commit``, the project's: ``(folder, branch)``."""
    root = Path(source)
    folder, branch = root.parent / f"{root.name}-{name}", f"{BRANCHES}{name}"
    delegate_candidate.git(source, "worktree", "add", "--quiet", "--no-track", "-b", branch, str(folder), commit)
    return str(folder), branch


def removed(source: str, folder: str, branch: str, start: str) -> bool:
    """Remove the worktree ``folder``, and the branch when it is still at ``start``, the commit it was made at, and no checkout holds it; what is
    gone already stays gone. Whether the branch is deleted: git compares and deletes in one step, so a commit made on it keeps it."""
    with contextlib.suppress(OSError, ValueError):
        delegate_candidate.git(source, "worktree", "remove", "--force", folder)
    shutil.rmtree(folder, ignore_errors=True)
    ref = f"refs/heads/{branch}"
    try:
        if not start or delegate_candidate.git(source, "for-each-ref", "--format=%(worktreepath)", ref).strip():
            return False
        delegate_candidate.git(source, "update-ref", "-d", ref, start)
    except (OSError, ValueError):
        return False
    return True


def committed(folder: str, rec: store.Delegate, paths: Sequence[str], unfinished: str = "") -> tuple[str, list[str]]:
    """Commit ``paths``, each by name, in the worktree ``folder`` as the delegate: the commit (empty when none of them differs) and the paths git
    would not take. A path with nothing there that git does not hold, a file made and removed again within the run, is neither. ``unfinished`` is
    the state of a run that did not finish, which the message then says. No hook of the repository runs and nothing is signed. ValueError when git
    cannot commit."""
    left, why = [], ""
    for path in paths:
        try:
            if not os.path.lexists(Path(folder) / path) and not delegate_candidate.git(folder, "--literal-pathspecs", "ls-files", "-z", "--", path):
                continue
            delegate_candidate.git(folder, "--literal-pathspecs", "add", "--", path)
        except ValueError as error:
            left.append(path)
            why = str(error)
    if not delegate_candidate.git(folder, "diff", "--cached", "--name-only", "-z"):
        if left:
            raise ValueError(why)
        return "", left
    who = {f"GIT_{role}_{field}": value for role in ("AUTHOR", "COMMITTER")
           for field, value in (("NAME", rec.name), ("EMAIL", f"{rec.name}@{IDENTITY}"))}
    subject = " ".join(rec.task.split())[:60]
    head = f"delegate {rec.name}: {subject}"
    standing = (f"UNFINISHED, the run ended {unfinished}: work of a local coding agent for {rec.lead}, kept as it stood, neither finished nor accepted."
                if unfinished else f"Work of a local coding agent for {rec.lead}, not yet reviewed by it.")
    delegate_candidate.git(folder, "-c", "commit.gpgSign=false", "commit", "--quiet", "--no-verify", "-m", f"{head}\n\n{standing}", env=who)
    return _head(folder), left


def settled(rec: store.Delegate, state: str, applied: Sequence[str] | None) -> tuple[str, str, dict[str, str]]:
    """What becomes of the worktree of a run that ended in ``state``: ``(state, what the lead is told, the branch and commit kept)``.
    ``applied`` are the paths a sandboxed run published there, None when the run left no word of them. What it published (with no sandbox, or no
    word, all that differs) is committed on its branch. A worktree with work that is not committed is left, and a `done` or `partial` run none of
    whose work could be committed ends `unapplied`. For a run that has not written its result: a finished run is settled once."""
    kept = {"branch": "", "commit": ""}
    if not Path(rec.workdir).is_dir():
        return state, "", kept
    try:
        paths = list(applied) if rec.sandboxed and applied is not None else changed(rec.workdir)
        sha, left = committed(rec.workdir, rec, paths, "" if state in FINISHED else state) if paths else ("", [])
    except (OSError, ValueError) as error:
        told = f"its work could not be committed ({error}), so it is left in the worktree {rec.workdir} on branch {rec.branch}"
        return "unapplied" if state in FINISHED else state, told, {**kept, "branch": rec.branch}
    gone = not left and removed(rec.source, rec.workdir, rec.branch, "" if sha else rec.commit)
    if not sha:
        if gone:
            return state, "nothing was committed, and its worktree and branch are removed", kept
        return state, f"nothing was committed by it; its worktree is removed and branch {rec.branch} is kept", {**kept, "branch": rec.branch}
    review = (f"a review is waiting for you: branch {rec.branch} at {sha}" if state in FINISHED else
              f"its unfinished work is committed, not accepted, on branch {rec.branch} at {sha}")
    where = (f"{len(left)} file(s) git would not take are not in it and stay in the worktree {rec.workdir}" if left else
             "the worktree is removed")
    told = f"{review}. Review it, grade it (`poolhouse-delegate grade {rec.name} ...`) and land it yourself: nothing was merged or pushed, and {where}"
    return state, told, {**kept, "branch": rec.branch, "commit": sha}

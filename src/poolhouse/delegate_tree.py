"""The worktree and branch of a delegate that edits a git project, which the launcher makes beside it from the commit it is at, so that the lead's
checkout is never written.

What the run changed is committed there by name, however it ended: a run that was done, or one that failed, was stopped or whose self-check did not
accept it, whose commit says it is unfinished. The branch is left for the session that started the delegate to review, grade and land. The worktree
is removed once what it held is committed, and so is a branch that got no commit; a worktree whose work git could not commit is left as it is, and a
file git would not take is kept as a patch. Nothing is merged or pushed, no hook of the repository runs and no git configuration is written.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from poolhouse import delegate_candidate, delegate_store as store, secretpaths
from poolhouse.isolation.history import IDENTITY
from poolhouse.workspace import nudge_fence

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


def committed(folder: str, rec: store.Delegate, paths: Sequence[str], unfinished: str = "",
              said: Sequence[str] = ()) -> tuple[str, list[str]]:
    """Commit ``paths``, each by name, in the worktree ``folder`` as the delegate: the commit (empty when none of them differs) and the paths git
    would not take. ``unfinished`` is the state of a run that did not finish, which the message then says; ``said`` are the messages of the
    delegate's own commits, kept in it. No hook of the repository runs and nothing is signed. ValueError when git cannot commit."""
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
    head = f"delegate {rec.name}: {subject}"
    standing = (f"UNFINISHED, the run ended {unfinished}: work of a local coding agent for {rec.lead}, kept as it stood, neither finished nor accepted."
                if unfinished else f"Work of a local coding agent for {rec.lead}, not yet reviewed by it.")
    own = "".join(f"\n\nA commit the delegate made, in its words:\n{nudge_fence.clean(each)}" for each in said)
    delegate_candidate.git(folder, "-c", "commit.gpgSign=false", "commit", "--quiet", "--no-verify", "-m", f"{head}\n\n{standing}{own}", env=who)
    return _head(folder), left


def _patched(rec: store.Delegate, names: Sequence[str]) -> str:
    """A patch of the files ``names`` of the worktree, which git would not commit, kept in the delegate's folder; empty when there is none."""
    if not names:
        return ""
    with contextlib.suppress(ValueError):
        delegate_candidate.git(rec.workdir, "--literal-pathspecs", "add", "--force", "--intent-to-add", "--", *names)
    patch = store.folder(rec.name) / "uncommitted.patch"
    patch.write_bytes(delegate_candidate.git(rec.workdir, "--literal-pathspecs", "diff", "--binary", "HEAD", "--", *names))
    return str(patch)


def settled(rec: store.Delegate, state: str, applied: Sequence[str] | None, said: Sequence[str] = ()) -> tuple[str, str, dict[str, str]]:
    """What becomes of the worktree of a run that ended in ``state``: ``(state, what the lead is told, the branch, commit and patch kept)``.
    ``applied`` are the paths a sandboxed run published there, None when the run left no word of them, and ``said`` the messages of the
    delegate's own commits. What it published (with no sandbox, or no word, all that differs) is committed on its branch; a `done` or `partial`
    run whose work cannot be committed ends `unapplied`, and the worktree of work that is not committed is left."""
    kept = {"branch": "", "commit": "", "patch": ""}
    if not Path(rec.workdir).is_dir():
        return state, "", kept
    try:
        paths = list(applied) if rec.sandboxed and applied is not None else changed(rec.workdir)
        sha, left = committed(rec.workdir, rec, paths, "" if state in FINISHED else state, said) if paths else ("", [])
        kept["patch"] = _patched(rec, left)
    except (OSError, ValueError) as error:
        told = f"its work could not be committed ({error}), so it is left in the worktree {rec.workdir} on branch {rec.branch}"
        return "unapplied" if state in FINISHED else state, told, {**kept, "branch": rec.branch}
    removed(rec.source, rec.workdir, rec.branch, keep_branch=bool(sha))
    patch = f"; {len(left)} file(s) git would not take are not in it, kept at {kept['patch']}" if left else ""
    if not sha:
        return state, f"nothing was committed, and its worktree and branch are removed{patch}", kept
    review = (f"a review is waiting for you: branch {rec.branch} at {sha}" if state in FINISHED else
              f"its unfinished work is committed, not accepted, on branch {rec.branch} at {sha}")
    told = (f"{review}{patch}. Review it, grade it (`poolhouse-delegate grade {rec.name} ...`) and land it yourself: nothing was merged or "
            "pushed, and the worktree is removed")
    return state, told, {**kept, "branch": rec.branch, "commit": sha}

"""Ends the test jobs a stopped subagent left running or queued."""

from __future__ import annotations

import sys
from pathlib import Path

import workspace_hook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def linked_worktree(root: Path) -> bool:
    """Whether ``root`` is a linked worktree (its .git is a file), not a primary checkout."""
    return (root / ".git").is_file()


def mine(spec: object, name: str, worktree: Path | None) -> bool:
    """Whether a job belongs to the subagent: it owns it, or the job runs in the subagent's own linked worktree."""
    if not isinstance(spec, dict):
        return False
    owner = spec.get("owner")
    if name and isinstance(owner, dict) and owner.get("id") == name:
        return True
    return worktree is not None and linked_worktree(worktree) and spec.get("root") == str(worktree)


def jobs_of(root: Path):
    """The job table of the project that ``root`` belongs to, or None when it has no table yet."""
    import testjobs

    from poolhouse.activity import reuse
    from poolhouse.workspace import testruns
    jobs = testjobs.Jobs(reuse.reuse_base() / testruns.scope(root))
    return jobs if jobs.folder.is_dir() else None


def cancel(stage: str, cwd: str, name: str, environment: dict) -> list[str]:
    """Cancel every queued or running test job of the subagent ``name`` that stopped in ``cwd``; returns the ids cancelled.

    A job that cannot be cancelled is reported as a warning and as an `announce blocked`.
    """
    if not cwd or not Path(cwd).is_dir():
        return []
    top = workspace_hook.attempt(["git", "-C", cwd, "rev-parse", "--show-toplevel"], stage)
    root = Path(top.stdout.strip()).resolve() if top is not None and top.returncode == 0 and top.stdout.strip() else None
    if root is None:
        return []
    cancelled, failed = [], []
    try:
        import testjobs
        jobs = jobs_of(root)
        for job in jobs.ids() if jobs is not None else []:
            if jobs.read(job, "status.json").get("state") not in ("queued", "running"):
                continue
            spec = jobs.read(job, "spec.json")
            if not mine(spec, name, root):
                continue
            try:
                jobs.cancel(job, name)
                cancelled.append(job)
            except (OSError, ValueError, KeyError, testjobs.Refused) as error:
                failed.append(f"{job}: {type(error).__name__}: {error}")
    except (ImportError, OSError, ValueError, RuntimeError) as error:
        failed.append(f"test jobs unreadable: {type(error).__name__}: {error}")
    for reason in failed:
        workspace_hook.warning(stage, f"could not cancel a test job of {name}: {reason}")
    if failed:
        workspace_hook.run(["poolhouse-workspace", "announce", "blocked",
                            f"{name} stopped with test jobs left: {failed[0]}"[:200]], stage, environment=environment)
    return cancelled

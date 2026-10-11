"""Execute claimed tasks with task lease heartbeats."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.errors import Error
from poolhouse.workspace import task_worktrees, testruns, worktree_lifecycle


@dataclass(frozen=True)
class Settings:
    stopped: Callable[[], bool] = lambda: False
    heartbeat_s: float = 20.0
    wall_s: float | None = None


def execute(s: Session, base: Path, task_id: str, allocation_id: str, run: Callable, **options: Any) -> dict[str, Any]:
    """Claim one queued task, check out its worktree and persist its proposal or actionable blockage."""
    settings, tasks = Settings(**options), Tasks(s)
    task = tasks.get(task_id)
    if task['state'] != 'queued':
        raise ValueError('only a queued task may start')
    limits = [value for value in (task['limits'].get('max_wall_s'), settings.wall_s) if value is not None]
    seconds = min(limits) if limits else None
    tasks.claim(task_id, allocation_id)
    done, cancel = threading.Event(), threading.Event()
    deadline = time.monotonic() + seconds if seconds is not None else None
    failure: list[str] = []

    def supervise() -> None:
        next_heartbeat = time.monotonic()
        while not done.wait(0.05):
            if settings.stopped() or (deadline is not None and time.monotonic() >= deadline):
                failure.append('Worker stopped' if settings.stopped() else 'Task wall time limit reached')
                cancel.set()
                return
            if time.monotonic() >= next_heartbeat:
                try:
                    tasks.heartbeat(task_id)
                except (Error, OSError, RuntimeError, ValueError) as error:
                    failure.append(f'Task lease heartbeat failed: {error}')
                    cancel.set()
                    return
                next_heartbeat = time.monotonic() + settings.heartbeat_s

    def checkpoint(value: dict[str, Any]) -> dict[str, Any]:
        if cancel.is_set():
            raise RuntimeError(failure[0])
        return testruns.checkpoint(tasks, task_id, value)

    watcher = threading.Thread(target=supervise, daemon=True)
    watcher.start()
    marker = worktree_lifecycle.assigned_file(base, s.name)
    try:
        scope = task_worktrees.activate(s, tasks.get(task_id))
        project = Path(scope['project']).resolve(strict=True)
        marker.write_text(str(project), encoding='utf-8')
        proposal = run(tasks.get(task_id), project, cancel.is_set, checkpoint)
        if failure:
            raise RuntimeError(failure[0])
        return testruns.submit(tasks, task_id, proposal)
    except (Error, OSError, RuntimeError, ValueError) as error:
        reason = failure[0] if failure else str(error) or type(error).__name__
        return tasks.block(task_id, reason[:2000])
    finally:
        marker.unlink(missing_ok=True)
        done.set()
        watcher.join(timeout=1)

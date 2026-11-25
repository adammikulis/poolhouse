"""Task-scoped management of existing project workers."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from poolhouse.board.session import Session
from poolhouse.errors import Denied
from poolhouse.workspace import localagent as la, localloop, localstart
from poolhouse.workspace.task_authority import authorize

__all__ = ["start", "stop"]


def start(s: Session, base: Path, worker: str, task: str) -> localstart.Started:
    """Start the assigned worker with its saved model, session and execution limits."""
    runner, spec = authorize(s, base, worker, task)
    if set(spec['capabilities']) - {'coding'}:
        raise Denied('native task launch cannot satisfy the requested capabilities')
    if la.alive(runner):
        if runner.profile != 'coding':
            raise Denied('stop the existing worker before changing its task harness')
        return localstart.Started(runner.name, runner.pid, runner.model_name, runner.role, already=True, identity=runner.identity)
    caps = asdict(localloop.caps_of(runner))
    ask = localstart.Ask(model=runner.model, name=runner.name, role=runner.role,
                         effort=runner.effort, max_effort=runner.max_effort, profile='coding',
                         ctx=runner.ctx, project=runner.project, orders_from=runner.orders_from,
                         harness=runner.harness, max_output_tokens=runner.max_output_tokens, task_caps=caps)
    return localstart.start(s, ask, base=base, reuse=True)


def stop(s: Session, base: Path, worker: str, task: str) -> localstart.Stopped:
    """Stop only the existing worker assigned to the caller's active task."""
    authorize(s, base, worker, task)
    return localstart.stop(base, worker)

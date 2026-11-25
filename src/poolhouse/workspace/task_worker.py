"""Local coding workers consume task assignments on the board and preserve blocked outcomes."""

from __future__ import annotations

import contextlib
import time
from pathlib import Path
from typing import Any

from poolhouse.board.client import NodeError
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.workspace import localagent as la, localloop, localslot, task_coding, task_runtime
from poolhouse.workspace.task_scheduler import execution_config

__all__ = ["assigned", "run"]


def assigned(s: Session, runner: la.Agent, slot: str) -> dict[str, Any] | None:
    """The oldest queued task assigned to this worker on its model slot lease, with its allocation, or None."""
    found = []
    for task in Tasks(s).list()['tasks']:
        if task['state'] != 'queued':
            continue
        for assignment in reversed(task['assignments']):
            if assignment['worker'] == runner.identity and assignment['lease'] == slot:
                found.append({'task': task['id'], 'allocation_id': assignment['id'], 'created_at': task['created_at'],
                              'allocations': task['allocations']})
                break
    return min(found, key=lambda row: row['created_at'], default=None)


def _verified(runner: la.Agent, allocation: dict[str, Any]) -> None:
    """Refuse an allocation made for another configuration of the worker than the one it runs with now."""
    made = next((row for row in allocation['allocations'] if row['id'] == allocation['allocation_id']), None)
    if made is None or made.get('execution_config') != execution_config(runner):
        raise Denied('the worker execution configuration changed after allocation')


def run(base: Path, name: str) -> None:
    """Execute assigned tasks until the worker's maintained stop flag is set."""
    status = la.Status(base, name)
    held, slot, keeper = None, '', contextlib.ExitStack()
    try:
        while not la.stop_file(base, name).exists():
            if la.pause_file(base, name).exists():
                status.update(state='paused', detail='Project tasks wait until resumed')
                time.sleep(1)
                continue
            agent = la.load(base, name)
            if agent is None:
                raise ValueError('the registered local worker is missing')
            if agent.harness not in ('pi', 'claude'):
                raise ValueError('project task work needs a supported coding harness')
            s = la.session_of(agent)
            if held is None:
                held = localloop.lease_model(agent)
                slot = localslot.acquire(s, agent)
                keeper.enter_context(localslot.kept(s, slot))
                status.update(lease=held.lease, slot=slot)
            allocation = assigned(s, agent, slot)
            if not allocation:
                status.update(state='idle', detail='Waiting for a task assignment')
                time.sleep(1)
                continue
            status.update(state='working', detail='Executing project task', task=allocation['task'])
            _verified(agent, allocation)

            def native(task, project, stopped, checkpoint, bound=(agent, s)):
                runner, board = bound
                return task_coding.perform(board, base, runner, task, task_coding.Run(project, stopped, checkpoint))

            outcome = task_runtime.execute(s, base, allocation['task'], allocation['allocation_id'], native,
                                           stopped=lambda: la.stop_file(base, name).exists(),
                                           wall_s=localloop.caps_of(agent).seconds)
            status.update(state=outcome.get('state', 'review'), detail=outcome.get('blocked_reason', 'Awaiting independent review'),
                          task=allocation['task'])
    finally:
        status.update(state='stopped', detail='Project worker stopped')
        keeper.close()
        try:
            if slot:
                localslot.release(la.session_of(la.load(base, name)), slot)
            if held is not None:
                held.release()
        except (NodeError, OSError, RuntimeError) as error:
            status.update(detail=f'Project worker stopped; lease cleanup failed: {error}')
            raise
        status.update(lease={}, slot='')

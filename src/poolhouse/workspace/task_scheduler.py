"""Registered parents allocate ready tasks to their local workers and land what is accepted."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from poolhouse import activity
from poolhouse.board.client import NodeError
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.serve import broker_wire
from poolhouse.serve.process import pid_exists, started_at
from poolhouse.workspace import (
    localagent as la,
    localslot,
    task_authority,
    task_integration,
    task_worktrees,
)

__all__ = ["assign_next", "execution_config", "integrate_completed", "watch"]

CONFIG = ('model', 'harness', 'ctx', 'effort', 'max_effort', 'max_output_tokens', 'role', 'profile', 'project')


def execution_config(runner: la.Agent) -> dict[str, Any]:
    """The part of a worker's saved configuration an allocation is made for."""
    return {key: getattr(runner, key) for key in CONFIG}


def _now() -> int:
    return int(time.time() * 1000)


def _runtime() -> Any:
    try:
        return broker_wire.status(start=False).get('runtime')
    except (OSError, RuntimeError):
        return None


def _ready(tasks: list[dict[str, Any]], runner: la.Agent, parent: str, model: str, wanted: str) -> list[dict[str, Any]]:
    """The queued tasks the worker may take, oldest first."""
    found = [row for row in tasks if (not wanted or row['id'] == wanted) and row['state'] == 'queued'
             and all(dep['state'] == 'completed' for dep in row['dependencies'])
             and set(row['capabilities']) <= {runner.profile}
             and row['limits'].get('model', model) == model
             and (runner.identity in row['assignees'] or row['created_by'] == parent or not row['assignees'])]
    return sorted(found, key=lambda row: row['created_at'])


def assign_next(s: Session, base: Path, worker: str, *, task: str = '') -> dict[str, Any] | None:
    """Prepare and allocate the oldest eligible queued task without retrying blocked work; the allocation, or None."""
    runner = task_authority.runner_of(base, s, worker)
    slot = localslot.held(s, runner.identity)
    if slot is None:
        return None
    model = next(r['model'] for r in slot['resources'] if r['type'] == 'model_slot')
    tasks = Tasks(s)
    parent = next((a.parent for a in s.agents() if a.name == runner.identity), '')
    for row in _ready(tasks.list()['tasks'], runner, parent, model, task):
        try:
            _, spec = task_authority.authorize(s, base, worker, row['id'])
            scope = task_worktrees.plan(s.name, runner, spec) if runner.profile == 'coding' else None
            allocation = tasks.assign(row['id'], runner.identity, slot['id'], [runner.profile])
        except (Denied, ValueError):
            continue
        if scope is not None:
            tasks.scope(row['id'], scope)
        provenance = {'execution_config': execution_config(runner), 'broker_runtime': _runtime(), 'worker': runner.identity,
                      'interpreter': sys.executable, 'python': sys.version.split()[0], 'scheduler_pid': os.getpid()}
        tasks.record(row['id'], 'allocation', allocation['allocation_id'], provenance)
        return {**allocation, **provenance, 'project': (scope or {}).get('project', runner.project)}
    return None


def integrate_completed(s: Session, base: Path, worker: str) -> list[dict[str, Any]]:
    """Attempt each independently accepted review of the worker's tasks once and preserve its outcome."""
    runner = task_authority.runner_of(base, s, worker)
    parent = next((a.parent for a in s.agents() if a.name == runner.identity), '')
    if parent != s.name:
        raise Denied('integration polling requires the registered worker parent')
    tasks, results = Tasks(s), []
    for summary in tasks.list()['tasks']:
        if summary['state'] != 'accepted' or summary.get('worker') != runner.identity:
            continue
        task = tasks.get(summary['id'])
        review = task.get('review') or {}
        if not review.get('accepted') or review.get('outcome') != 'accepted':
            continue
        ident = 'integration-attempt:' + review['review_hash']
        old = next((row for row in task.get('integration_attempts', []) if row['id'] == ident), None)
        if old:
            live = bool(old.get('owner_pid') and old.get('owner_started') and pid_exists(old['owner_pid'])
                        and int(started_at(old['owner_pid']) or 0) == old['owner_started'])
            if old['state'] == 'started' and old['owner'] == s.name and not live:
                outcome = {'state': 'blocked', 'reason':
                           'The previous integration owner is no longer live; preserve and inspect its candidate before recovery.'}
                tasks.record(task['id'], 'attempt', ident, {'state': 'blocked', 'outcome': outcome, 'finished': _now(),
                                                            'recovery_required': True})
                results.append(outcome)
            continue
        tasks.record(task['id'], 'attempt', ident, {'task': task['id'], 'worker': runner.identity, 'owner': s.name,
                                                    'review_hash': review['review_hash'], 'state': 'started', 'started': _now(),
                                                    'owner_pid': os.getpid(), 'owner_started': int(started_at(os.getpid()) or 0)})
        try:
            outcome = task_integration.integrate(s, base, task['id'])
        except (NodeError, OSError, ValueError, RuntimeError) as error:
            outcome = {'state': 'blocked', 'reason': str(error)}
        tasks.record(task['id'], 'attempt', ident, {'state': outcome['state'], 'outcome': task_integration.public(outcome, base), 'finished': _now()})
        activity.record('agent.integration', actor=s.name, subject=task['title'], outcome=outcome['state'],
                        refs={'task': task['id'], 'worker': runner.identity},
                        meta={'review_hash': review['review_hash'], 'reason': outcome.get('reason', '')})
        results.append(outcome)
    return results


def watch(s: Session, base: Path, name: str, *, task: str = '') -> int:
    """Continuously assign ready tasks using the registered parent's own session."""
    while not la.stop_file(base, name).exists():
        agent = la.load(base, name)
        if agent is None:
            raise ValueError('the local worker is missing')
        if not task:
            integrate_completed(s, base, agent.identity)
        if la.status_of(base, name).get('state') == 'idle':
            assign_next(s, base, agent.identity, task=task)
        time.sleep(1)
    return 0

"""Authenticated relocation of inactive task source bindings."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from poolhouse import worktreerules
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.board.taskspec import text
from poolhouse.command import flag
from poolhouse.errors import Denied
from poolhouse.files import read_json, write_json
from poolhouse.lock import held
from poolhouse.workspace import integration_git as repo, limits, localagent as la, task_authority
from poolhouse.workspace.task_worktree_recovery import dematerialize, inactive

__all__ = ["TABLE", "rebind"]


def _worker(s: Session, base: Path, worker: str) -> la.Agent:
    runner = task_authority.runner_of(base, s, worker)
    parent = next((a.parent for a in s.agents() if a.name == runner.identity), '')
    if parent != s.name:
        raise Denied('only the authenticated registered worker parent can relocate its source')
    return runner


def _scopes(s: Session, base: Path, runner: la.Agent, sources: tuple[str, ...], target: Path) -> list[dict[str, Any]]:
    """The tasks of the worker whose checkout is bound to one of ``sources``, checked for relocation to ``target``."""
    tasks = Tasks(s)
    found = []
    for summary in tasks.list()['tasks']:
        scope = summary.get('scope') or {}
        if scope.get('worker') == runner.identity and scope.get('source_project') in sources:
            found.append(tasks.get(summary['id']))
    found.sort(key=lambda task: task['id'])
    if not found:
        raise Denied('the worker has no pending source bindings')
    for task in found:
        scope = task['scope']
        if s.name != scope['owner'] or s.name != task['created_by']:
            raise Denied('source recovery requires ownership and creation of every affected task')
        inactive(base, task, scope)
        if scope.get('state') != 'reserved' or Path(scope['project']).exists() or Path(scope['project']).is_symlink():
            raise Denied('dematerialize every affected task checkout before relocating its source')
        if not repo.ancestor(target, scope['baseline_commit'], repo.git(target, 'rev-parse', 'HEAD')):
            raise Denied('the replacement source does not contain the original task baseline')
    return found


def _source(source: str, target: Path) -> None:
    old, new = worktreerules.checkouts(source), worktreerules.checkouts(target)
    if not old or old[0] != Path(source).resolve() or not new or new[0] != target or old[1] != new[1]:
        raise Denied('the replacement must be a checkout of the same physical Git repository')
    repo.clean(target)


def _now() -> int:
    return int(time.time() * 1000)


def _log(base: Path, name: str) -> Path:
    return la.folder(base) / f'{name}.recovery.json'


def rebind(s: Session, base: Path, worker: str, project: str, reason: str) -> dict[str, Any]:
    """Relocate one stopped worker's pending source scopes within its physical Git repository."""
    reason = text(reason, 'recovery reason', 2000)
    if not project:
        raise ValueError('a replacement source checkout is required')
    target = Path(la.check_project(project, base))
    with held(la.folder(base) / 'start.lock'):
        runner = _worker(s, base, worker)
        previous = read_json(_log(base, runner.name), {})
        same = previous.get('target') == str(target)
        if same and previous.get('state') == 'committed' and runner.project == str(target):
            checkout = worktreerules.checkouts(target)
            if not checkout or checkout[0] != target or str(checkout[1]) != previous['primary']:
                raise Denied('the completed source recovery repository changed')
            repo.clean(target)
            if any((row.get('scope') or {}).get('worker') == runner.identity
                   and (row.get('scope') or {}).get('source_project') == previous['source'] for row in Tasks(s).list()['tasks']):
                raise Denied('the worker still has task scopes bound to its old source')
            return {'worker': runner.identity, 'project': str(target), 'tasks': previous['tasks'],
                    'recovery': previous['id'], 'verified': True}
        resuming = same and previous.get('state') == 'prepared'
        source = previous['source'] if resuming else runner.project
        if source == str(target) or runner.project not in (source, str(target)):
            raise Denied('the saved worker source changed outside its recorded recovery')
        _source(source, target)
        found = _scopes(s, base, runner, (source, str(target)) if resuming else (source,), target)
        if resuming:
            log = previous
        else:
            log = {'id': 'source-recovery:' + uuid4().hex, 'worker': runner.identity, 'owner': s.name, 'source': source,
                   'target': str(target), 'primary': str(worktreerules.checkouts(target)[1]), 'reason': reason, 'at': _now(),
                   'state': 'prepared', 'tasks': [task['id'] for task in found]}
            write_json(_log(base, runner.name), log)
        tasks = Tasks(s)
        for task in found:
            if task['scope']['source_project'] == log['source']:
                tasks.scope(task['id'], {**task['scope'], 'source_project': str(target)})
            if not any(row.get('recovery') == log['id'] for row in task.get('integration_events', [])):
                tasks.record(task['id'], 'event', 'checkpoint:' + uuid4().hex,
                             {'summary': log['reason'], 'transition': 'rebind-source', 'authorized_by': log['owner'],
                              'recovery': log['id'], 'source_project': log['source'], 'target_project': log['target'],
                              'baseline_commit': task['scope']['baseline_commit'], 'at': _now()})
        la.save(base, replace(runner, project=str(target)))
        write_json(_log(base, runner.name), {**log, 'state': 'committed'})
    return {'worker': runner.identity, 'project': str(target), 'tasks': log['tasks'], 'recovery': log['id'], 'verified': True}


TABLE = [
    ("task-rebind-source", "relocate a stopped worker source, preserving pending task grants", [flag("worker"), flag("project"), flag("reason")],
     lambda a, s: rebind(s, limits.root(), a.worker, a.project, a.reason)),
    ("task-dematerialize", "remove an unchanged inactive task checkout, preserving its pending history", [flag("id"), flag("reason")],
     lambda a, s: dematerialize(s, limits.root(), a.id, a.reason)),
]

"""Parent-authorized recovery of unchanged inactive task checkouts."""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from poolhouse import worktreerules
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.board.taskspec import text
from poolhouse.errors import Denied
from poolhouse.serve.process import pid_exists, started_at
from poolhouse.workspace import integration_git as repo, localagent as la, task_authority

__all__ = ["dematerialize", "exclusive", "inactive"]


def _covers(held: str, wanted: str) -> bool:
    here, there = Path(held), Path(wanted)
    return here == there or here in there.parents or there in here.parents


def inactive(base: Path, task: dict[str, Any], scope: dict[str, Any]) -> None:
    """Refuse a task that has a proposal, a live lease, a live worker or a live scheduler."""
    if task['state'] not in ('blocked', 'queued') or (task.get('proposal') or {}).get('id'):
        raise Denied('only an unsubmitted pending task can dematerialize its checkout')
    if (task.get('lease') or {}).get('active'):
        raise Denied('the task execution lease remains active')
    for name in la.names(base):
        runner = la.load(base, name)
        if runner and runner.identity == scope['worker'] and runner.pid and pid_exists(runner.pid) \
                and started_at(runner.pid) == runner.process_started:
            raise Denied('the assigned native worker remains live')
    for value in task.get('allocations', []):
        if value.get('worker') != scope['worker']:
            raise Denied('the task has another worker allocation requiring recovery')
        if value.get('scheduler_pid') and pid_exists(value['scheduler_pid']):
            raise Denied('the task scheduler remains live')


@contextmanager
def exclusive(s: Session, scope: dict[str, Any]):
    """The inactive checkout and branch of a task through recovery: nobody but the parent and the stopped worker holds them and
    no area reservation lies in the checkout or its repository; the claims of those two are given up once recovery ended well."""
    if s.name != scope['owner']:
        raise Denied('inactive worktree recovery requires its registered parent')
    mine = (s.name, scope['worker'])
    primary = worktreerules.checkouts(scope['source_project'])[1]
    for claim in s.claims():
        if claim.kind == 'area' and (_covers(claim.key, scope['project']) or _covers(claim.key, str(primary))):
            raise Denied('an area reservation in the checkout or its repository must be released first')
        held = (claim.kind == 'worktree' and _covers(claim.key, scope['project'])) or (claim.kind == 'branch' and claim.key == scope['branch'])
        if held and claim.owner not in mine:
            raise Denied('the inactive checkout has another live ownership assignment')
    yield
    for kind, key in (('worktree', scope['project']), ('branch', scope['branch'])):
        owner = next((c.owner for c in s.claims(kind) if c.key == key), '')
        if owner in mine:
            s.release(kind, key, held_by='' if owner == s.name else owner)


def dematerialize(s: Session, base: Path, ident: str, reason: str) -> dict[str, Any]:
    """Remove an unchanged inactive task checkout while retaining its pending task history."""
    reason = text(reason, 'recovery reason', 2000)
    tasks = Tasks(s)
    task = tasks.get(ident)
    scope = task.get('scope') or {}
    state = scope.get('state')
    if not scope or state not in ('active', 'reserved'):
        raise Denied('the task has no recoverable checkout')
    runner = task_authority.runner_of(base, s, scope['worker'])
    parent = next((a.parent for a in s.agents() if a.name == runner.identity), '')
    if s.name != scope['owner'] or s.name != parent:
        raise Denied('only the actual registered task parent can recover its checkout')
    inactive(base, task, scope)
    source, target = Path(scope['source_project']), Path(scope['project'])
    checkout = worktreerules.checkouts(source)
    if not checkout or checkout[0] != source.resolve():
        raise Denied('the source repository changed')
    primary = checkout[1]
    with exclusive(s, scope):
        if target.exists():
            trees = worktreerules.checkouts(target)
            if not trees or trees != (target.resolve(), primary) or target.resolve() == primary:
                raise Denied('the assigned task checkout registration changed')
            if repo.git(target, 'branch', '--show-current') != scope['branch'] \
                    or repo.git(target, 'rev-parse', 'HEAD') != scope['baseline_commit']:
                raise Denied('the task checkout contains unique committed work')
        landed = repo.git(primary, 'rev-parse', 'HEAD')
        repo.remove_merged(primary, target, scope['branch'], landed)
    tasks.scope(task['id'], {**scope, 'state': 'reserved', 'dematerialized_at': int(time.time() * 1000)})
    tasks.record(task['id'], 'event', 'checkpoint:' + uuid4().hex,
                 {'summary': reason, 'transition': 'dematerialize', 'authorized_by': s.name,
                  'baseline_commit': scope['baseline_commit'], 'at': int(time.time() * 1000)})
    return {'task': task['id'], 'state': task['state'], 'cleanup_verified': True}

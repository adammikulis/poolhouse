"""Inactive unchanged task recovery preserves pending task history."""

from pathlib import Path

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import board as _board_fixture

from poolhouse import worktreerules
from poolhouse.errors import Denied
from poolhouse.workspace import (
    integration_git as repo,
    task_scheduler,
    task_worktree_recovery as recovery,
    task_worktrees,
)

board = _board_fixture


def claims_of(kit):
    return {(c.kind, c.key): c.owner for c in kit.parent.claims()}


def worker_takes(kit, allocation=None):
    """The worker claims its task and takes its checkout."""
    kit.work.claim(kit.task['id'], (allocation or kit.allocation)['allocation_id'])
    task_worktrees.activate(kit.child, kit.work.get(kit.task['id']))


@pytest.fixture
def inactive(board, monkeypatch):
    worker_takes(board)
    board.work.block(board.task['id'], 'Native turn cancelled')
    monkeypatch.setattr(recovery, 'pid_exists', lambda pid: False)
    return board


def scope_of(kit):
    return kit.board.get(kit.task['id'])['scope']


def test_dematerialization_preserves_blockage_and_checkpoints_and_can_rematerialize(inactive):
    kit = inactive
    before = kit.board.get(kit.task['id'])
    result = recovery.dematerialize(kit.parent, kit.base, kit.task['id'], 'Stopped worker verified')
    after = kit.board.get(kit.task['id'])
    assert result['cleanup_verified'] and result['state'] == 'blocked'
    assert after['blocked_reason'] == before['blocked_reason']
    assert after['failures'] == before['failures']
    assert after['checkpoints'] == before['checkpoints']
    assert [row['transition'] for row in after['integration_events']] == ['dematerialize']
    scope = after['scope']
    assert scope['state'] == 'reserved' and not Path(scope['project']).exists()
    assert ('worktree', scope['project']) not in claims_of(kit)
    kit.board.resume(kit.task['id'], 'Ready for reassignment')
    again = task_scheduler.assign_next(kit.parent, kit.base, kit.worker_id, task=kit.task['id'])
    assert scope_of(kit)['project'] == scope['project'] and scope_of(kit)['state'] == 'reserved'
    worker_takes(kit, again)
    assert Path(scope['project']).exists()
    assert claims_of(kit)[('worktree', scope['project'])] == kit.worker_id


@pytest.mark.parametrize('kind', ['worker', 'scheduler', 'working', 'foreign', 'area', 'dirty', 'ignored', 'commit', 'authority'])
def test_recovery_refuses_live_or_unique_work(inactive, monkeypatch, kind):
    kit = inactive
    target = Path(kit.allocation['project'])
    if kind == 'worker':
        monkeypatch.setattr(recovery, 'pid_exists', lambda value: value == 555)
        monkeypatch.setattr(recovery, 'started_at', lambda value: 42)
    elif kind == 'scheduler':
        pid = kit.allocation['scheduler_pid']
        monkeypatch.setattr(recovery, 'pid_exists', lambda value: value == pid)
    elif kind == 'working':
        kit.board.resume(kit.task['id'], 'Back to the queue')
        worker_takes(kit, task_scheduler.assign_next(kit.parent, kit.base, kit.worker_id, task=kit.task['id']))
    elif kind == 'foreign':
        kit.parent.release('worktree', str(target), held_by=kit.worker_id)
        kit.reviewer.claim('worktree', str(target))
    elif kind == 'area':
        kit.parent.release('worktree', str(target), held_by=kit.worker_id)
        kit.parent.claim('area', str(target / 'code.py'))
    elif kind in ('dirty', 'ignored'):
        (target / ('code.py' if kind == 'dirty' else '.private-state')).write_text('preserve\n')
    elif kind == 'commit':
        (target / 'code.py').write_text('unique = True\n')
        repo.git(target, 'add', '--', 'code.py')
        repo.git(target, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fix: unique task work')
    with pytest.raises((Denied, RuntimeError)):
        recovery.dematerialize(kit.child if kind == 'authority' else kit.parent, kit.base, kit.task['id'], 'Inactive recovery request')
    assert target.exists()
    assert kit.board.get(kit.task['id'])['state'] in ('blocked', 'working')


def test_failed_cleanup_restores_exact_prior_claims(inactive):
    kit = inactive
    target = Path(kit.allocation['project'])
    before = claims_of(kit)
    (target / 'code.py').write_text('pending = True\n')
    with pytest.raises(Denied):
        recovery.dematerialize(kit.parent, kit.base, kit.task['id'], 'Preserve pending files')
    assert claims_of(kit) == before


def test_a_successful_recovery_gives_up_the_stopped_workers_claims(inactive):
    kit = inactive
    scope = scope_of(kit)
    assert claims_of(kit)[('worktree', scope['project'])] == kit.worker_id
    recovery.dematerialize(kit.parent, kit.base, kit.task['id'], 'Stopped worker verified')
    assert ('worktree', scope['project']) not in claims_of(kit) and ('branch', scope['branch']) not in claims_of(kit)


def test_missing_checkout_preserves_pending_history(inactive):
    kit = inactive
    before = kit.board.get(kit.task['id'])
    primary = worktreerules.checkouts(kit.source)[1]
    scope = before['scope']
    repo.remove_merged(primary, Path(scope['project']), scope['branch'], repo.git(primary, 'rev-parse', 'HEAD'))
    result = recovery.dematerialize(kit.parent, kit.base, kit.task['id'], 'Missing checkout')
    after = kit.board.get(kit.task['id'])
    assert result['cleanup_verified'] and after['state'] == before['state'] and after['checkpoints'] == before['checkpoints']


def test_existing_reserved_checkout_keeps_its_baseline(inactive):
    kit = inactive
    kit.board.resume(kit.task['id'], 'Prepare existing reservation')
    primary = worktreerules.checkouts(kit.source)[1]
    scope = scope_of(kit)
    target = Path(scope['project'])
    moved = primary.parent / 'moved-task-checkout'
    repo.git(primary, 'worktree', 'move', str(target), str(moved))
    kit.board.scope(kit.task['id'], {**scope, 'state': 'reserved', 'project': str(moved)})
    (primary / 'code.py').write_text('updated = True\n')
    repo.git(primary, 'add', '--', 'code.py')
    repo.git(primary, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fix: advance baseline')
    again = task_scheduler.assign_next(kit.parent, kit.base, kit.worker_id, task=kit.task['id'])
    assert again['project'] == str(moved)
    assert scope_of(kit)['baseline_commit'] == scope['baseline_commit']

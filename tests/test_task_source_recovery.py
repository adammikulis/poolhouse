"""Offline source recovery retains pending tasks and immutable resource history."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import board as _board_fixture

from poolhouse import worktreerules
from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.workspace import (
    integration_git as repo,
    localagent,
    task_scheduler,
    task_source_recovery as recovery,
    task_worktree_recovery,
    task_worktrees,
)

board = _board_fixture


def rebind_events(kit):
    return [row for row in kit.board.get(kit.task['id'])['integration_events'] if row['transition'] == 'rebind-source']


def log_of(kit):
    return json.loads((localagent.folder(kit.base) / 'native-worker.recovery.json').read_text())


@pytest.fixture
def stopped(board, monkeypatch):
    board.work.claim(board.task['id'], board.allocation['allocation_id'])
    task_worktrees.activate(board.child, board.work.get(board.task['id']))
    board.work.block(board.task['id'], 'Pending native task')
    monkeypatch.setattr(task_worktree_recovery, 'pid_exists', lambda pid: False)
    task_worktree_recovery.dematerialize(board.parent, board.base, board.task['id'], 'Worker stopped')
    board.target = worktreerules.checkouts(board.source)[1]
    return board


def test_source_rebind_preserves_specs_states_and_historical_allocations(stopped):
    kit = stopped
    before = kit.board.get(kit.task['id'])
    result = recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Retire source anchor')
    after = kit.board.get(kit.task['id'])
    assert result['verified'] and result['tasks'] == [kit.task['id']]
    for key in ('state', 'blocked_reason', 'failures', 'title', 'acceptance', 'assignees', 'checkpoints', 'allocations'):
        assert after[key] == before[key]
    assert after['scope'] == {**before['scope'], 'source_project': str(kit.target)}
    assert localagent.load(kit.base, 'native-worker').project == str(kit.target)
    assert [row['source_project'] for row in rebind_events(kit)] == [str(kit.source)]
    repeated = recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Verify recovery')
    assert repeated['recovery'] == result['recovery']
    assert kit.board.get(kit.task['id'])['integration_events'] == after['integration_events']


def test_a_rebound_task_is_assigned_and_taken_again_from_the_new_source(stopped):
    kit = stopped
    recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Retire source anchor')
    kit.board.resume(kit.task['id'], 'Ready for reassignment')
    again = task_scheduler.assign_next(kit.parent, kit.base, kit.worker_id, task=kit.task['id'])
    kit.work.claim(kit.task['id'], again['allocation_id'])
    scope = kit.board.get(kit.task['id'])['scope']
    assert scope['source_project'] == str(kit.target) and scope['state'] == 'reserved'


@pytest.mark.redteam
@pytest.mark.parametrize('kind', ['caller', 'live', 'materialized', 'repository', 'baseline', 'owner', 'creator', 'retired'])
def test_source_rebind_refuses_unrelated_or_active_assignments(stopped, monkeypatch, kind):
    kit = stopped
    target, caller = kit.target, kit.parent
    scope = kit.board.get(kit.task['id'])['scope']
    if kind == 'caller':
        caller = kit.child
    elif kind == 'live':
        monkeypatch.setattr(task_worktree_recovery, 'pid_exists', lambda pid: pid == 555)
        monkeypatch.setattr(task_worktree_recovery, 'started_at', lambda pid: 42)
    elif kind == 'repository':
        target = kit.source.parent / 'unrelated'
        target.mkdir()
        repo.git(target, 'init')
    elif kind == 'materialized':
        Path(scope['project']).mkdir()
    elif kind == 'baseline':
        kit.board.scope(kit.task['id'], {**scope, 'baseline_commit': 'a' * 40})
    elif kind == 'owner':
        kit.board.scope(kit.task['id'], {**scope, 'owner': kit.reviewer.name})
    elif kind == 'creator':
        other = Tasks(kit.reviewer).create(dict(kit.spec, title='Another task', source_key='repo:demo/sim:issue:43'))
        task_scheduler.assign_next(kit.parent, kit.base, kit.worker_id, task=other['id'])
    else:
        kit.parent.retire(kit.worker_id)
    before = localagent.load(kit.base, 'native-worker')
    with pytest.raises((Denied, RuntimeError)):
        recovery.rebind(caller, kit.base, kit.worker_id, str(target), 'Offline recovery')
    assert localagent.load(kit.base, 'native-worker') == before
    assert not rebind_events(kit)


def test_prepared_rebind_resumes_after_worker_file_interruption(stopped, monkeypatch):
    kit = stopped
    real_save = localagent.save

    def crash(base, worker):
        real_save(base, worker)
        raise SystemExit('Interrupted after worker update')
    monkeypatch.setattr(localagent, 'save', crash)
    with pytest.raises(SystemExit):
        recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Resume source recovery')
    assert log_of(kit)['state'] == 'prepared'
    monkeypatch.setattr(localagent, 'save', real_save)
    result = recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Resume source recovery')
    assert result['verified'] and log_of(kit)['state'] == 'committed' and log_of(kit)['id'] == result['recovery']
    assert len(rebind_events(kit)) == 1


def test_failed_node_update_leaves_the_worker_and_resumes_the_prepared_log(stopped, monkeypatch):
    kit = stopped
    original, real_scope = localagent.load(kit.base, 'native-worker'), Tasks.scope

    def fail(self, ident, scope):
        raise RuntimeError('Node write interrupted')
    monkeypatch.setattr(Tasks, 'scope', fail)
    with pytest.raises(RuntimeError, match='interrupted'):
        recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Recover source')
    assert localagent.load(kit.base, 'native-worker') == original
    assert kit.board.get(kit.task['id'])['scope']['source_project'] == str(kit.source) and log_of(kit)['state'] == 'prepared'
    monkeypatch.setattr(Tasks, 'scope', real_scope)
    assert recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Recover source')['verified']
    assert kit.board.get(kit.task['id'])['scope']['source_project'] == str(kit.target)


@pytest.mark.redteam
@pytest.mark.parametrize('drift', ['repository', 'dirty'])
def test_committed_retry_revalidates_repository_and_clean_source(stopped, monkeypatch, drift):
    kit = stopped
    recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Initial recovery')
    if drift == 'dirty':
        (kit.target / 'code.py').write_text('changed = True\n')
    else:
        actual = worktreerules.checkouts
        monkeypatch.setattr(worktreerules, 'checkouts', lambda target:
                            (kit.target, kit.target.parent / 'different-primary') if Path(target) == kit.target
                            else actual(target))
    with pytest.raises(Denied):
        recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Verify recovery')


@pytest.mark.redteam
def test_a_worker_retired_after_recovery_cannot_be_recovered_again(stopped):
    kit = stopped
    assert recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Authorized recovery')['verified']
    kit.parent.retire(kit.worker_id)
    with pytest.raises(Denied):
        recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Revoked worker')


@pytest.mark.redteam
def test_cancelled_node_update_restores_the_worker_and_resumes_the_prepared_log(stopped, monkeypatch):
    kit = stopped
    original = localagent.load(kit.base, 'native-worker')
    before = kit.board.get(kit.task['id'])
    real_scope = Tasks.scope

    def interrupt(self, ident, scope):
        real_scope(self, ident, scope)
        raise KeyboardInterrupt('Recovery cancelled')
    monkeypatch.setattr(Tasks, 'scope', interrupt)
    with pytest.raises(KeyboardInterrupt, match='cancelled'):
        recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Source recovery')
    monkeypatch.setattr(Tasks, 'scope', real_scope)
    assert localagent.load(kit.base, 'native-worker') == original
    after = kit.board.get(kit.task['id'])
    assert after['state'] == before['state'] and after['checkpoints'] == before['checkpoints']
    assert log_of(kit)['state'] == 'prepared' and not rebind_events(kit)
    assert recovery.rebind(kit.parent, kit.base, kit.worker_id, str(kit.target), 'Resume source recovery')['verified']
    assert len(rebind_events(kit)) == 1 and kit.board.get(kit.task['id'])['scope']['source_project'] == str(kit.target)
    assert localagent.load(kit.base, 'native-worker') == replace(original, project=str(kit.target))

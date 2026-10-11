"""Workers select queued allocations on their model slot and persist proposals on the board."""

from dataclasses import replace
from pathlib import Path

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import board

from poolhouse.workspace import (
    localagent as la,
    localslot,
    task_runtime,
    task_scheduler,
    task_worker,
)

__all__ = ['board']


def slot_of(kit):
    return localslot.held(kit.child, kit.worker_id)['id']


def test_actual_board_assignment_proposal_and_no_repeat(board):
    allocation = task_worker.assigned(board.child, board.runner, slot_of(board))
    assert allocation['allocation_id'] == board.allocation['allocation_id'] and allocation['task'] == board.task['id']

    def run(task, project, stopped, checkpoint):
        assert str(project) == str(Path(board.allocation['project']).resolve())
        checkpoint({'summary': 'Native tool completed', 'commit': 'a' * 40})
        return {'artifacts': {'result.md': 'a' * 64},
                'checks': [{'name': 'Harness returned', 'passed': True}],
                'summary': 'Proposed work', 'provenance': {'commit': 'a' * 40}}
    proposal = task_runtime.execute(board.child, board.base, board.task['id'], allocation['allocation_id'], run)
    task = board.board.get(board.task['id'])
    assert task['state'] == 'review'
    assert task['proposal']['id'] == proposal['id']
    assert task['checkpoints'][0]['summary'] == 'Native tool completed'
    assert task_worker.assigned(board.child, board.runner, slot_of(board)) is None


def test_actual_board_block_does_not_reenter_worker(board):
    def run(*_):
        raise RuntimeError('Approval expired')
    task_runtime.execute(board.child, board.base, board.task['id'], board.allocation['allocation_id'], run)
    task = board.board.get(board.task['id'])
    assert task['state'] == 'blocked'
    assert task['blocked_reason'] == 'Approval expired'
    assert task_worker.assigned(board.child, board.runner, slot_of(board)) is None


def test_restarted_worker_waits_for_its_own_slot_and_keeps_old_allocation(board):
    assert task_worker.assigned(board.child, board.runner, 'replacement-slot') is None
    assert task_worker.assigned(board.child, board.runner, slot_of(board))['allocation_id'] == board.allocation['allocation_id']


def test_scheduler_reallocates_queued_work_after_worker_restart(board):
    localslot.release(board.child, slot_of(board))
    assert task_scheduler.assign_next(board.parent, board.base, board.worker_id) is None
    replacement = localslot.acquire(board.child, board.runner)
    allocation = task_scheduler.assign_next(board.parent, board.base, board.worker_id)
    assert allocation['allocation_id'] != board.allocation['allocation_id'] and allocation['lease_id'] == replacement
    assert task_worker.assigned(board.child, board.runner, replacement)['allocation_id'] == allocation['allocation_id']
    board.work.claim(board.task['id'], allocation['allocation_id'])
    assert board.board.get(board.task['id'])['state'] == 'working'
    ids = [row['id'] for row in board.board.get(board.task['id'])['allocations']]
    assert board.allocation['allocation_id'] in ids and allocation['allocation_id'] in ids


def test_scheduler_takes_the_next_task_and_an_older_blocked_one_is_not_retried(board):
    board.work.claim(board.task['id'], board.allocation['allocation_id'])
    board.work.block(board.task['id'], 'Prior task awaiting review')
    later = board.board.create({**board.spec, 'source_key': 'current-project'})
    allocation = task_scheduler.assign_next(board.parent, board.base, board.worker_id)
    assert allocation['task'] == later['id']
    assert allocation['project'] != str(board.source)
    assert board.board.get(later['id'])['scope']['source_project'] == str(board.source)
    board.work.claim(later['id'], allocation['allocation_id'])
    assert board.board.get(later['id'])['worker'] == board.worker_id


def test_failed_lease_cleanup_publishes_stopped_status_and_preserves_grant(board, monkeypatch):
    agent = replace(la.load(board.base, 'native-worker'), harness='claude')
    la.save(board.base, agent)

    def release():
        raise RuntimeError('Access to registered broker denied')
    held = task_worker.localloop.Held(None, {'id': 'native-grant'}, release)
    monkeypatch.setattr(task_worker.localloop, 'lease_model', lambda *_: held)
    monkeypatch.setattr(task_worker.localslot, 'acquire', lambda *_: slot_of(board))
    with pytest.raises(RuntimeError, match='Access to registered broker denied') as raised:
        task_worker.run(board.base, agent.name)
    status = la.status_of(board.base, agent.name)
    assert status['state'] == 'stopped'
    assert status['lease']['id'] == 'native-grant'
    assert 'lease cleanup failed' in status['detail']
    assert 'execution configuration changed' in str(raised.value.__context__)
    assert board.board.get(board.task['id'])['state'] == 'queued'


def test_pi_worker_is_accepted_and_waits_for_a_allocation(board, monkeypatch, tmp_path):
    agent = la.load(board.base, 'native-worker')
    la.save(board.base, replace(agent, harness='pi'))
    stop = tmp_path / 'stop'
    held = task_worker.localloop.Held(None, {'id': 'native-grant'}, lambda: None)
    monkeypatch.setattr(task_worker.la, 'stop_file', lambda *_: stop)
    monkeypatch.setattr(task_worker.localloop, 'lease_model', lambda *_: held)
    monkeypatch.setattr(task_worker.localslot, 'acquire', lambda *_: slot_of(board))
    monkeypatch.setattr(task_worker, 'assigned', lambda *_: None)
    monkeypatch.setattr(task_worker.time, 'sleep', lambda _: stop.touch())
    task_worker.run(board.base, agent.name)
    assert la.status_of(board.base, agent.name)['state'] == 'stopped'

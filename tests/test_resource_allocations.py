"""Task grants are assigned by genuine parents and revalidated against the node's live model lease."""
from dataclasses import replace

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import board  # noqa: F401

from poolhouse.board.client import Denied
from poolhouse.errors import Denied as Refused
from poolhouse.workspace import localagent, localslot, task_scheduler, task_worker


def test_a_worker_holding_its_slot_is_allocated_and_a_changed_configuration_or_lease_is_refused(board):  # noqa: F811
    allocation = board.allocation
    assert allocation['worker'] == board.worker_id and allocation['lease_id'] == localslot.held(board.child, board.worker_id)['id']
    assert allocation['execution_config']['harness'] == 'pi' and allocation['scheduler_pid']
    stored = board.board.get(board.task['id'])['allocations']
    assert [row['id'] for row in stored] == [allocation['allocation_id']]
    seen = {'task': board.task['id'], 'allocation_id': allocation['allocation_id'], 'allocations': stored}
    task_worker._verified(board.runner, seen)
    for fields in ({'model': 'other'}, {'harness': 'claude'}, {'ctx': 65536}, {'effort': 'high'},
                   {'max_effort': 'low'}, {'max_output_tokens': 16384}, {'role': 'manual'}):
        with pytest.raises(Refused, match='execution configuration changed'):
            task_worker._verified(replace(board.runner, **fields), seen)
    with pytest.raises(Refused, match='configuration changed'):
        task_worker._verified(board.runner, {**seen, 'allocation_id': 'another'})


def test_only_the_parent_assigns_and_only_a_lease_the_worker_holds_is_assigned(board):  # noqa: F811
    task = board.board.create({**board.spec, 'source_key': 'another'})['id']
    slot = localslot.held(board.child, board.worker_id)['id']
    with pytest.raises(Denied, match='parent'):
        board.work.assign(task, board.worker_id, slot, ['coding'])
    other = board.node.member('other-worker', parent=board.lead)
    foreign = board.session(other).call('lease_acquire', resources=[{'type': 'model_slot', 'model': 'other-model'}], ttl_s=5000)
    with pytest.raises(Denied, match='active managed grant'):
        board.board.assign(task, board.worker_id, foreign['id'], ['coding'])
    assert board.board.assign(task, board.worker_id, slot, ['coding'])['lease_id'] == slot


def test_a_released_slot_ends_the_allocation_and_the_worker_cannot_claim(board):  # noqa: F811
    localslot.release(board.child, localslot.held(board.child, board.worker_id)['id'])
    with pytest.raises(Denied, match='active managed grant'):
        board.work.claim(board.task['id'], board.allocation['allocation_id'])
    assert board.board.get(board.task['id'])['state'] == 'queued'
    assert localslot.held(board.child, board.worker_id) is None
    assert task_scheduler.assign_next(board.parent, board.base, board.worker_id) is None


def test_the_slot_of_a_model_is_held_by_one_session_on_a_device(board):  # noqa: F811
    again = localagent.load(board.base, 'native-worker')
    with pytest.raises(RuntimeError, match='another session holds'):
        localslot.acquire(board.session(board.node.member('rival', parent=board.lead)), again)

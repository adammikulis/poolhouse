"""Execution claims on the board, checks out the worktree, keeps leases alive and stops blocked work."""

import threading
from pathlib import Path

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import board  # noqa: F401

from poolhouse.board.tasks import Tasks
from poolhouse.workspace import localagent as la, task_runtime, worktree_lifecycle

PROPOSAL = {'artifacts': {'report.md': 'a' * 64}, 'checks': [{'name': 'Harness returned', 'passed': True}], 'summary': 'Proposed'}


def execute(kit, run, **options):
    return task_runtime.execute(kit.child, kit.base, kit.task['id'], kit.allocation['allocation_id'], run, **options)


def test_the_run_gets_the_allocated_checkout_and_its_checkpoints_and_proposal_are_the_workers(board):  # noqa: F811
    seen = {}

    def run(task, project, stopped, checkpoint):
        seen['project'], seen['marker'] = project, worktree_lifecycle.assigned(board.base, board.worker_id)
        checkpoint({'summary': 'Native tool completed', 'commit': 'a' * 40})
        return PROPOSAL

    proposal = execute(board, run)
    task = board.board.get(board.task['id'])
    assert seen['project'] == Path(board.allocation['project']).resolve() == Path(task['scope']['project']).resolve()
    assert seen['marker'] == str(seen['project']) and worktree_lifecycle.assigned(board.base, board.worker_id) == ''
    assert task['state'] == 'review' and task['proposal']['id'] == proposal['id'] and task['worker'] == board.worker_id
    assert task['checkpoints'][0]['summary'] == 'Native tool completed'
    assert task['scope']['state'] == 'active'


def test_a_failed_heartbeat_cancels_and_blocks_without_resubmission(board, monkeypatch):  # noqa: F811
    def heartbeat(self, ident):
        raise RuntimeError('Broker holder expired')
    monkeypatch.setattr(Tasks, 'heartbeat', heartbeat)

    def run(task, project, stopped, checkpoint):
        for _ in range(100):
            if stopped():
                return PROPOSAL
            threading.Event().wait(0.01)
        pytest.fail('Native task was not cancelled')

    result = execute(board, run)
    assert result['state'] == 'blocked' and 'Broker holder expired' in result['blocked_reason']
    with pytest.raises(ValueError, match='only a queued'):
        execute(board, run)
    assert board.board.get(board.task['id'])['proposal'] is None


def test_an_unusable_checkout_blocks_the_task_after_its_claim(board):  # noqa: F811
    stray = Path(board.allocation['project'])
    stray.mkdir()
    (stray / 'stray.txt').write_text('unassigned files')
    result = execute(board, lambda *_: pytest.fail('run'))
    assert result['state'] == 'blocked' and 'unassigned files' in result['blocked_reason']


@pytest.mark.parametrize('cause', ['Task wall time limit reached', 'Task lease heartbeat failed: socket denied'])
def test_native_cancellation_preserves_the_supervisor_cause(board, monkeypatch, cause):  # noqa: F811
    options = {}
    if 'heartbeat' in cause:
        def heartbeat(self, ident):
            raise RuntimeError('socket denied')
        monkeypatch.setattr(Tasks, 'heartbeat', heartbeat)
    else:
        options['wall_s'] = 0.2

    def run(task, project, stopped, checkpoint):
        while not stopped():
            threading.Event().wait(0.01)
        raise RuntimeError('Native coding turn cancelled or ended without an answer')

    assert execute(board, run, **options)['blocked_reason'] == cause


def test_runtime_has_no_implicit_wall_cap(board):  # noqa: F811
    def run(task, project, stopped, checkpoint):
        threading.Event().wait(0.1)
        assert not stopped()
        return PROPOSAL

    assert board.board.get(board.task['id'])['limits'].get('max_wall_s') is None
    assert 'artifacts' in execute(board, run)


def test_a_stopped_worker_blocks_its_task_with_that_cause(board):  # noqa: F811
    la.stop_file(board.base, 'native-worker').write_text('stop')

    def run(task, project, stopped, checkpoint):
        while not stopped():
            threading.Event().wait(0.01)
        raise RuntimeError('ended')

    result = execute(board, run, stopped=lambda: la.stop_file(board.base, 'native-worker').exists())
    assert result['state'] == 'blocked' and result['blocked_reason'] == 'Worker stopped'

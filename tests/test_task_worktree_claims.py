"""A worker that claimed its task holds its task checkout; the harness hooks refuse every other session there."""

from dataclasses import replace
from pathlib import Path

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import board, worker_on  # noqa: F401

from poolhouse import harnesshook
from poolhouse.board.tasks import Tasks
from poolhouse.net import git
from poolhouse.workspace import localagent, task_scheduler, task_worktrees

pytestmark = pytest.mark.redteam


@pytest.fixture
def native_task(board, tmp_path):  # noqa: F811
    repository, source = tmp_path / 'handoff-repository', tmp_path / 'handoff-source'
    repository.mkdir()
    git.run(['init', str(repository)])
    (repository / 'code.py').write_text('ORIGINAL = True\n')
    git.run(['add', 'code.py'], cwd=repository)
    git.run(['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'baseline'], cwd=repository)
    git.run(['worktree', 'add', '-b', 'source', str(source)], cwd=repository)
    localagent.save(board.base, replace(board.runner, project=str(source)))
    board.task = board.board.create({**board.spec, 'source_key': 'native-handoff'})
    board.allocation = task_scheduler.assign_next(board.parent, board.base, board.worker_id, task=board.task['id'])
    board.scope = board.board.get(board.task['id'])['scope']
    board.project = Path(board.scope['project'])
    return board


def work(board):  # noqa: F811
    """The worker claims the task and takes its checkout, as the runtime does."""
    board.work.claim(board.task['id'], board.allocation['allocation_id'])
    task_worktrees.activate(board.child, board.work.get(board.task['id']))


def edit(board, actor):  # noqa: F811
    target = board.project / 'code.py'
    payload = {'tool_name': 'Edit', 'tool_input': {'file_path': str(target),
               'old_string': 'ORIGINAL', 'new_string': 'UPDATED'}, 'cwd': str(board.project)}
    result = harnesshook.pre(payload, harnesshook.Rail('plan-and-go', actor,
                                                     roots=[str(board.project)], wait_s=0))
    decision = result['hookSpecificOutput']['permissionDecision']
    if decision == 'allow':
        target.write_text(target.read_text().replace('ORIGINAL', 'UPDATED'))
    return decision


def test_the_worker_that_claimed_its_task_holds_the_checkout_and_only_it_may_edit_there(native_task):
    board = native_task  # noqa: F811
    work(board)
    other = board.node.member('unrelated', parent=board.lead)
    assert edit(board, other.name) == 'deny'
    assert (board.project / 'code.py').read_text() == 'ORIGINAL = True\n'
    assert edit(board, board.worker_id) == 'allow'
    held = {(c.kind, c.key): c.owner for c in board.child.claims()}
    assert held[('worktree', str(board.project))] == board.worker_id
    assert held[('branch', board.scope['branch'])] == board.worker_id
    assert (board.project / 'code.py').read_text() == 'UPDATED = True\n'


def test_a_claim_of_the_parent_on_a_file_of_the_checkout_refuses_the_worker(native_task):
    board = native_task  # noqa: F811
    work(board)
    board.parent.claim('area', str(board.project / 'code.py'))
    assert edit(board, board.worker_id) == 'deny'
    assert (board.project / 'code.py').read_text() == 'ORIGINAL = True\n'
    board.parent.release('area', str(board.project / 'code.py'))
    assert edit(board, board.worker_id) == 'allow'


def test_a_checkout_nobody_claimed_yet_is_not_held_for_the_assigned_worker(native_task):
    board = native_task  # noqa: F811
    assert not board.project.exists() and not [c for c in board.child.claims() if c.key == str(board.project)]


def test_unassigned_task_is_open_to_any_worker_and_assignment_closes_it_to_those_of_other_parents(board):  # noqa: F811
    other, _ = worker_on(board, board.source, name='second-worker', model='other-model', parent=board.stranger)
    assigned = board.board.create({**board.spec, 'source_key': 'assigned-task', 'assignees': [board.worker_id]})
    open_task = board.board.create({**board.spec, 'source_key': 'open-task'})
    second = task_scheduler.assign_next(board.reviewer, board.base, other.name)
    assert second is not None and second['task'] == open_task['id']
    first = Tasks(board.parent).get(assigned['id'])
    assert first['state'] == 'queued' and [a['worker'] for a in first['assignments']] == []
    mine = task_scheduler.assign_next(board.parent, board.base, board.worker_id, task=assigned['id'])
    assert mine['task'] == assigned['id']

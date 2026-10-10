"""The per-project enforcement mode: open by default, strict restores the delegated-authority checks."""

import pytest
from taskboard_kit import accepted, board as _board_fixture, proposed

from poolhouse.workspace import enforcement, task_scope
from poolhouse.workspace.identity import Denied

board = _board_fixture
pytestmark = pytest.mark.redteam


def mode(kit, new):
    return enforcement.set_mode(kit.ws, kit.parent, '', new)


def task(kit, key, **extra):
    return kit.board.create(kit.parent, {**kit.spec, 'source_key': key, **extra})


def test_default_mode_is_open(board):
    assert enforcement.current(board.ws, '') == {'project': '', 'mode': 'open'}
    assert enforcement.mode(board.ws, {'key': 'anything'}) == 'open'


def test_unassigned_task_by_an_agent_is_claimable_by_members_only_when_open(board):
    member = board.ws.auth(board.agent('member'))
    open_task = task(board, 'unassigned')
    assert task_scope.eligible(board.ws, member, open_task)
    mode(board, 'strict')
    assert not task_scope.eligible(board.ws, member, open_task)
    assert task_scope.eligible(board.ws, board.ws.auth(board.child), open_task)
    mode(board, 'open')
    assert task_scope.eligible(board.ws, member, open_task)


def test_assignee_needs_the_task_project_only_when_strict(board):
    assert task(board, 'open-assignee', assignees=[board.worker_id])['assignees'] == [board.worker_id]
    mode(board, 'strict')
    with pytest.raises(Denied):
        task(board, 'strict-assignee', assignees=[board.worker_id])
    assert task(board, 'strict-plain')['assignees'] == []


def test_new_reviews_need_delegated_authority_only_when_strict(board):
    proposed(board)
    member = board.agent('member')
    mode(board, 'strict')
    with pytest.raises(Denied):
        board.board.review(member, board.task['id'], accepted())
    mode(board, 'open')
    assert board.board.review(member, board.task['id'], accepted())['accepted']


def test_promotion_keeps_in_flight_work_running(board):
    board.board.claim(board.child, board.task['id'], board.allocation['allocation_id'])
    mode(board, 'strict')
    board.board.heartbeat(board.child, board.task['id'])
    board.board.submit(board.child, board.task['id'], {
        'artifacts': {'replay.json': 'a' * 64}, 'checks': [{'name': 'Worker claims tests', 'passed': True}],
        'summary': 'proposed', 'provenance': {'commit': 'abc123', 'environment': 'native-python',
                                              'model': 'qwen', 'runtime': 'Agents SDK'}})
    assert board.board.get(board.parent, board.task['id'])['state'] == 'review'


def test_accepted_review_stays_valid_after_promotion(board):
    proposed(board)
    board.board.review(board.agent('member'), board.task['id'], accepted())
    mode(board, 'strict')
    assert board.board.assert_reviewer(board.agent('member2'), board.task['id'])['id'] == board.task['id']


def test_lead_promotes_and_demotes_and_every_change_is_audited(board):
    first = enforcement.set_mode(board.ws, board.parent, '', 'strict')
    assert first['from'] == 'open' and enforcement.mode(board.ws, {}) == 'strict'
    enforcement.set_mode(board.ws, board.parent, '', 'open')
    changes = [row for row in board.ws.audit_log.rows() if row['event'] == 'enforcement.set']
    assert [(row['who'], row['project'], row['from'], row['to']) for row in changes] == [
        ('lead', '', 'open', 'strict'), ('lead', '', 'strict', 'open')]
    assert all(row['ts'] for row in changes)


def test_a_helper_child_cannot_set_the_mode(board):
    with pytest.raises(Denied):
        enforcement.set_mode(board.ws, board.child, '', 'strict')
    assert enforcement.mode(board.ws, {}) == 'open'
    assert not [row for row in board.ws.audit_log.rows() if row['event'] == 'enforcement.set']


def test_the_modes_are_per_project(board):
    enforcement.set_mode(board.ws, board.parent, 'alpha', 'strict')
    assert enforcement.mode(board.ws, {'key': 'alpha'}) == 'strict'
    assert enforcement.mode(board.ws, {'key': 'beta'}) == 'open'
    assert enforcement.mode(board.ws, {}) == 'open'

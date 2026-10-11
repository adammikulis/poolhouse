"""Authenticated parent polling claims accepted reviews once before publication."""

import threading
from dataclasses import replace

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import accepted, board, proposed

from poolhouse.errors import Denied
from poolhouse.workspace import localagent, task_scheduler

__all__ = ['board']


def complete(kit):
    proposed(kit)
    kit.board.review(kit.task['id'], accepted())


def attempts(kit):
    return kit.board.get(kit.task['id'])['integration_attempts']


def test_parent_polls_accepted_review_once_and_records_the_attempt_on_the_task(board, monkeypatch):
    complete(board)
    calls = []
    monkeypatch.setattr(task_scheduler.task_integration, 'integrate',
                        lambda s, base, task: calls.append((s.name, task)) or {'state': 'published', 'commit': 'a' * 40})
    assert task_scheduler.integrate_completed(board.parent, board.base, board.worker_id)[0]['state'] == 'published'
    assert task_scheduler.integrate_completed(board.parent, board.base, board.worker_id) == []
    assert calls == [(board.lead.name, board.task['id'])]
    record = attempts(board)[0]
    assert record['owner'] == board.lead.name and record['state'] == 'published'
    assert record['review_hash'] == board.board.get(board.task['id'])['review']['review_hash']


@pytest.mark.parametrize('raises', [False, True])
def test_blocked_publication_is_not_retried(board, monkeypatch, raises):
    complete(board)
    calls = []
    def publish(*args):
        calls.append(args)
        if raises:
            raise RuntimeError('Development ownership unavailable')
        return {'state': 'blocked', 'reason': 'Required check failed'}
    monkeypatch.setattr(task_scheduler.task_integration, 'integrate', publish)
    assert task_scheduler.integrate_completed(board.parent, board.base, board.worker_id)[0]['state'] == 'blocked'
    assert task_scheduler.integrate_completed(board.parent, board.base, board.worker_id) == []
    assert len(calls) == 1 and attempts(board)[0]['state'] == 'blocked'


def test_proposed_work_is_not_published_before_independent_review(board, monkeypatch):
    proposed(board)
    monkeypatch.setattr(task_scheduler.task_integration, 'integrate', lambda *_: pytest.fail('unreviewed publication'))
    assert task_scheduler.integrate_completed(board.parent, board.base, board.worker_id) == []
    assert attempts(board) == []
    with pytest.raises(Denied, match='registered worker parent'):
        task_scheduler.integrate_completed(board.child, board.base, board.worker_id)


def test_concurrent_pollers_reserve_review_before_running_helper(board, monkeypatch):
    complete(board)
    started, release = threading.Event(), threading.Event()
    calls = []
    def publish(*args):
        calls.append(args)
        started.set()
        assert release.wait(10)
        return {'state': 'published'}
    monkeypatch.setattr(task_scheduler.task_integration, 'integrate', publish)
    results = []
    thread = threading.Thread(target=lambda: results.append(task_scheduler.integrate_completed(
        board.parent, board.base, board.worker_id)))
    thread.start()
    try:
        assert started.wait(10)
        assert attempts(board)[0]['state'] == 'started'
        assert task_scheduler.integrate_completed(board.parent, board.base, board.worker_id) == []
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and len(calls) == 1
    assert results == [[{'state': 'published'}]]


def test_dead_integration_owner_becomes_visible_recovery_without_blind_retry(board, monkeypatch):
    complete(board)
    review = board.board.get(board.task['id'])['review']
    board.board.record(board.task['id'], 'attempt', 'integration-attempt:' + review['review_hash'],
                       {'task': board.task['id'], 'worker': board.worker_id, 'owner': board.lead.name,
                        'review_hash': review['review_hash'], 'state': 'started', 'started': 1000,
                        'owner_pid': 999999, 'owner_started': 42})
    monkeypatch.setattr(task_scheduler, 'pid_exists', lambda pid: False)
    monkeypatch.setattr(task_scheduler.task_integration, 'integrate', lambda *_: pytest.fail('unsafe retry'))
    result = task_scheduler.integrate_completed(board.parent, board.base, board.worker_id)
    assert result[0]['state'] == 'blocked'
    assert attempts(board)[0]['recovery_required']
    assert board.board.get(board.task['id'])['state'] == 'accepted'
    assert task_scheduler.integrate_completed(board.parent, board.base, board.worker_id) == []


def test_chat_assignment_review_finishes_without_git_scope(board):
    complete(board)
    localagent.save(board.base, replace(localagent.load(board.base, 'native-worker'), profile='chat'))
    spec = dict(board.spec, title='Answer a question', source_key='chat:question', capabilities=['chat'])
    task = board.board.create(spec)
    allocation = task_scheduler.assign_next(board.parent, board.base, board.worker_id)
    assert allocation['task'] == task['id']
    assert allocation['worker'] == board.worker_id
    board.work.claim(task['id'], allocation['allocation_id'])
    board.work.submit(task['id'], {
        'artifacts': {'answer.txt': 'a' * 64},
        'checks': [{'name': 'Answer reviewed', 'passed': True}],
        'summary': 'Question answered',
        'provenance': {'environment': 'chat', 'model': 'qwen', 'runtime': 'Agents SDK'}})
    board.board.review(task['id'], accepted())
    done = board.board.get(task['id'])
    assert done['state'] == 'completed' and not done['scope']

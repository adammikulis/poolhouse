"""Authenticated agent interfaces operate on tasks without granting authority."""

import json

import pytest
from task_kit import accepted, board, proposed

from poolhouse import mcp
from poolhouse.board.client import Denied
from poolhouse.board.session import AGENT_ENV
from poolhouse.memory import vault
from poolhouse.workspace import task_outcomes, tools

__all__ = ['board']

pytest_plugins = ["node_kit"]


def actor(monkeypatch, who):
    monkeypatch.setenv(AGENT_ENV, who.name)


def test_cli_lists_and_reads_task_with_fixed_actor(board):
    result = board.node.cli('tasks', '--json', who=board.worker)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['tasks'][0]['id'] == board.task['id']
    result = board.node.cli('task', board.task['id'], '--json', who=board.worker)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['acceptance'] == ['Replay passes']
    result = board.node.cli('tasks', '--json')
    assert result.returncode == 3


def test_mcp_claim_checkpoint_submit_and_pending_review_share_actual_board(board, monkeypatch):
    actor(monkeypatch, board.worker)
    tools.workspace_task_claim(board.task['id'], board.allocation['allocation_id'])
    tools.workspace_task_heartbeat(board.task['id'])
    tools.workspace_task_checkpoint(board.task['id'], {'summary': 'Native replay collected'})
    tools.workspace_task_submit(board.task['id'], {'artifacts': {'replay.json': 'a' * 64},
                                                   'checks': [{'name': 'Worker check', 'passed': True}]})
    assert tools.workspace_task(board.task['id'])['checkpoints'][0]['summary'] == 'Native replay collected'
    actor(monkeypatch, board.lead)
    def locked(*args):
        raise vault.KeyUnavailable('isolated locked ledger')
    monkeypatch.setattr(task_outcomes.task_credit, 'verify_on_board', locked)
    board.tasks(board.lead).scope(board.task['id'], {'state': 'reserved'})
    result = tools.workspace_task_review(board.task['id'], accepted())
    assert result['outcome'] == 'accepted' and result['credit']['state'] == 'pending'
    # the task has a worktree, so it is accepted and only cleanup that proves the landing completes it
    assert tools.workspace_task(board.task['id'])['state'] == 'accepted'
    assert tools.workspace_task_credit(board.task['id'])['state'] == 'pending'


@pytest.mark.redteam
def test_mcp_cannot_self_review_or_claim_forged_resource(board, monkeypatch):
    actor(monkeypatch, board.worker)
    with pytest.raises(Denied):
        tools.workspace_task_claim(board.task['id'], 'foreign-allocation')
    assert tools.workspace_task(board.task['id'])['state'] == 'queued'
    tools.workspace_task_claim(board.task['id'], board.allocation['allocation_id'])
    tools.workspace_task_submit(board.task['id'], {'artifacts': {'replay.json': 'a' * 64},
                                                   'checks': [{'name': 'Worker check', 'passed': True}]})
    with pytest.raises(Denied):
        tools.workspace_task_review(board.task['id'], accepted())
    assert tools.workspace_task(board.task['id'])['state'] == 'review'


def test_mcp_exports_native_task_schemas_without_actor_or_permission_fields():
    listed = {tool.public()['name']: tool.public() for tool in mcp.TOOLS}
    for name in tools.HINTS:
        if name.startswith('workspace_task'):
            assert 'token' not in listed[name]['inputSchema']['properties']
            assert 'actor' not in listed[name]['inputSchema']['properties']
    assert 'workspace_task_create' not in listed
    assert listed['workspace_tasks']['annotations']['readOnlyHint']
    assert not listed['workspace_task_review']['annotations']['readOnlyHint']


@pytest.mark.redteam
def test_native_cli_review_stdin_preserves_independent_authority(board):
    proposed(board)
    refused = board.node.cli('task-review', board.task['id'], '-', '--json', who=board.worker, stdin=json.dumps(accepted()))
    assert refused.returncode == 3
    assert board.tasks(board.lead).get(board.task['id'])['state'] == 'review'
    malformed = board.node.cli('task-review', board.task['id'], '-', '--json', who=board.lead, stdin='[]')
    assert malformed.returncode != 0
    assert board.tasks(board.lead).get(board.task['id'])['review'] is None
    result = board.node.cli('task-review', board.task['id'], '-', '--json', who=board.lead, stdin=json.dumps(accepted()))
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response['outcome'] == 'accepted'
    assert response['credit']['state'] == 'pending'
    assert board.tasks(board.lead).get(board.task['id'])['state'] == 'completed'

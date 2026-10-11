"""Task-scoped existing worker launch preserves live grants and native execution limits."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401
from taskboard_kit import board

from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.net import git
from poolhouse.workspace import (
    localagent as la,
    localloop,
    localmodel,
    localstart,
    task_authority,
    task_caps,
    task_launch,
)

__all__ = ['board']


@pytest.fixture
def audit(board):
    """A worker of its own (a session with no parent) saved on the project, and a task of the lead that names it."""
    member = board.node.member('audit-worker')
    runner = la.Agent('audit-worker', 'qwen', identity=member.name, board=board.node.board, role='read-only',
                      project=str(board.source), ctx=131072, max_effort='low', harness='claude', max_output_tokens=12345)
    la.save(board.base, runner)
    task = board.board.create({**board.spec, 'source_key': 'audit-request', 'assignees': [member.name]})
    return board, runner, task


@pytest.mark.parametrize('harness', ['pi', 'claude', 'codex'])
def test_existing_nonchild_launch_preserves_identity_configuration_and_caps(audit, monkeypatch, harness):
    kit, runner, task = audit
    runner = replace(runner, harness=harness)
    la.save(kit.base, runner)
    monkeypatch.setattr(localstart.localmodel, 'choose', lambda *_args, **_kw: localmodel.Pick(ref='qwen', name='qwen'))
    monkeypatch.setattr(localstart.lp, 'admit', lambda *_: ('', ''))
    monkeypatch.setattr(localstart.jobs, 'detach', lambda *_args, **_kw: SimpleNamespace(pid=123, log='runtime.log'))
    monkeypatch.setattr(localstart, 'started_at', lambda _: 10)
    monkeypatch.setattr(localstart, '_register', lambda *_: pytest.fail('registered another session'))
    task_launch.start(kit.parent, kit.base, runner.name, task['id'])
    saved = la.load(kit.base, runner.name)
    assert saved.identity == runner.identity and saved.profile == 'coding' and saved.harness == harness
    assert saved.max_output_tokens == runner.max_output_tokens == 12345
    assert (saved.model, saved.role, saved.ctx, saved.max_effort, saved.orders_from) == (
        runner.model, runner.role, runner.ctx, runner.max_effort, runner.orders_from)
    assert localloop.caps_of(saved) == localloop.caps_of(runner)


@pytest.mark.redteam
@pytest.mark.parametrize('cause', ['caller', 'assignee', 'orders', 'source', 'board', 'active'])
def test_exact_task_authority_denies_foreign_or_changed_assignment(audit, cause, monkeypatch, tmp_path):
    kit, runner, task = audit
    caller, ident = kit.parent, task['id']
    if cause == 'caller':
        caller = kit.reviewer
    elif cause == 'assignee':
        ident = kit.board.create({**kit.spec, 'source_key': 'other-task'})['id']
    elif cause == 'orders':
        caller = kit.reviewer
        ident = Tasks(caller).create({**kit.spec, 'source_key': 'stranger-task', 'assignees': [runner.identity]})['id']
    elif cause == 'source':
        la.save(kit.base, replace(runner, project=str(kit.base.parent)))
    elif cause == 'board':
        elsewhere = tmp_path / 'elsewhere'
        elsewhere.mkdir()
        git.run(['init'], cwd=elsewhere)
        monkeypatch.delenv('POOLHOUSE_BOARD')
        la.save(kit.base, replace(runner, project=str(elsewhere)))
    else:
        la.Status(kit.base, runner.name).update(state='working', task='task-99')
    with pytest.raises(Denied, match="checkout of this board's project" if cause == 'board' else None):
        task_authority.authorize(caller, kit.base, runner.name, ident)


@pytest.mark.redteam
def test_native_tool_admission_refuses_exhausted_and_invalid_counter(tmp_path):
    path = tmp_path / 'counter.json'
    path.write_text(json.dumps({'version': 1, 'calls': 0, 'limit': 1}))
    assert task_caps.admit(path)
    assert not task_caps.admit(path)
    path.write_text(json.dumps({'version': 1, 'calls': False, 'limit': 1}))
    with pytest.raises(ValueError):
        task_caps.admit(path)


@pytest.mark.redteam
def test_fractional_saved_counter_cap_is_rejected():
    runner = la.Agent('audit-worker', 'qwen', extra={'task_caps': {'calls': 0.5}})
    with pytest.raises(ValueError):
        localloop.caps_of(runner)

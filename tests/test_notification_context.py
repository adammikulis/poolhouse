"""Notifications observe existing workspaces without opting unrelated projects in."""
import importlib.util
import uuid
from pathlib import Path

import pytest

from poolhouse import node_launch
from poolhouse.board import client
from poolhouse.net import git
from poolhouse.workspace import codex_hooks

pytestmark = pytest.mark.redteam
MISSING = 'this directory is not part of a project: register it with project_add'


def claude_hook():
    path = Path(__file__).resolve().parents[1] / 'scripts/hooks/workspace_hook.py'
    spec = importlib.util.spec_from_file_location('notification_workspace_hook', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[False, True], ids=["no-selector", "injected-board"])
def unknown_project(tmp_path, monkeypatch, request):
    root = tmp_path / 'unregistered'
    root.mkdir()
    git.run(['init', '-q', '-b', 'development'], cwd=root)
    monkeypatch.delenv('POOLHOUSE_BOARD', raising=False)
    if request.param:
        monkeypatch.setenv('POOLHOUSE_BOARD', 'injected-other-board')
    monkeypatch.setattr(node_launch, 'ensure_node', lambda *a, **k: pytest.fail('notification started daemon'))
    calls = []

    def exchange(self, request):
        calls.append(request)
        assert self.autostart is False and self.timeout <= 2.0
        assert request['method'] == 'project_resolve', 'notification mutated or queried another project'
        assert request['params'] == {'path': str(root.resolve())}
        return {'ok': False, 'error': {'code': 'denied', 'message': f'denied: {MISSING}'}}

    monkeypatch.setattr(client.Client, '_exchange', exchange)
    return root, calls


@pytest.mark.parametrize('stage', ['SessionStart', 'PostToolUse', 'SubagentStart'])
def test_codex_unknown_git_project_is_a_quiet_noop_without_registration(unknown_project, monkeypatch, stage):
    root, calls = unknown_project
    identifier = str(uuid.uuid4())
    monkeypatch.setenv('CODEX_THREAD_ID', identifier)
    assert codex_hooks.handle({'hook_event_name': stage, 'session_id': identifier, 'cwd': str(root)}) == {}
    assert [call['method'] for call in calls] == ['project_resolve']


@pytest.mark.parametrize('entry', ['session_environment', 'agent_name', 'name_session'])
def test_claude_unknown_git_project_is_a_quiet_noop_without_registration(unknown_project, capsys, entry):
    root, calls = unknown_project
    hook = claude_hook()
    event = {'session_id': 'native-test', 'cwd': str(root)}
    if entry == 'session_environment':
        assert hook.session_environment(event, 'SessionStart') is None
    elif entry == 'agent_name':
        assert hook.agent_name('native-test', str(root)) == ''
    else:
        assert hook.name_session({'POOLHOUSE_SESSION_ID': 'native-test'}, '', event) == ''
    assert [call['method'] for call in calls] == ['project_resolve']
    assert capsys.readouterr().err == ''


def test_legacy_nudge_hook_does_not_create_a_project(unknown_project, monkeypatch, capsys):
    import argparse

    from poolhouse.workspace import board_cli

    root, calls = unknown_project
    monkeypatch.chdir(root)
    assert board_cli.nudging(argparse.Namespace(hook='prompt', board='injected-board', agent='', token_file='')) == 0
    assert [call['method'] for call in calls] == ['project_resolve']
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('entry', ['codex', 'session_environment', 'agent_name', 'name_session', 'nudge'])
def test_offline_notification_never_starts_daemon_and_warns_without_credentials(tmp_path, monkeypatch, capsys, entry):
    import argparse
    import io
    import json
    import sys

    from poolhouse.workspace import board_cli

    monkeypatch.setattr(node_launch, 'ensure_node', lambda *a, **k: pytest.fail('notification started daemon'))
    def offline(self, request):
        assert self.autostart is False and self.timeout <= 2.0
        raise OSError('offline poolhouse-ws1.actor.syntheticSecret123')
    monkeypatch.setattr(client.Client, '_exchange', offline)
    identifier = str(uuid.uuid4())
    event = {'hook_event_name': 'SessionStart', 'session_id': identifier, 'cwd': str(tmp_path)}
    if entry == 'codex':
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(event)))
        assert codex_hooks.run(argparse.Namespace(worker=True, completion=False, assigned='')) == 0
    elif entry == 'nudge':
        assert board_cli.nudging(argparse.Namespace(hook='prompt', board='', agent='', token_file='')) == 0
    else:
        hook = claude_hook()
        if entry == 'session_environment':
            assert hook.session_environment(event, 'SessionStart') is None
        elif entry == 'agent_name':
            assert hook.agent_name(identifier, str(tmp_path)) == ''
        else:
            assert hook.name_session({'POOLHOUSE_SESSION_ID': identifier}, '', event) == ''
    output = capsys.readouterr()
    assert not output.out and 'offline' in output.err
    assert 'syntheticSecret123' not in output.err


@pytest.mark.parametrize('grant,message', [({'action': 'source_add'}, MISSING), (None, 'authentication refused')])
def test_project_authentication_refusal_is_not_treated_as_absence(tmp_path, monkeypatch, grant, message):
    from poolhouse.workspace.notification_context import existing_board

    def refused(self, request):
        return {'ok': False, 'error': {'code': 'denied', 'message': message, 'grant': grant}}
    monkeypatch.setattr(client.Client, '_exchange', refused)
    with pytest.raises(client.Denied):
        existing_board(client.Client(autostart=False), tmp_path)

"""Session-scoped notification readers on a node: the reader of a hook reads the inbox of the session its launcher named."""

import io
import json
from types import SimpleNamespace

import pytest
from harness_kit import SRC
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse import harnesshook
from poolhouse.board.client import Client
from poolhouse.errors import Denied
from poolhouse.net import git
from poolhouse.workspace import harness_session, notification_reader as reader


@pytest.fixture
def kit(workspace_node, tmp_path):  # noqa: F811
    lead = workspace_node.member('lead')
    kit = SimpleNamespace(node=workspace_node, root=tmp_path, lead=lead)
    kit.reader, kit.sender = (workspace_node.member(name, parent=lead) for name in ('reader', 'sender'))
    kit.r, kit.s = workspace_node.session(kit.reader), workspace_node.session(kit.sender)
    return kit


def test_the_reader_reports_what_waits_for_its_own_session_only(kit):
    assert reader.read(kit.reader.name, kit.root) == ''
    kit.s.post(kit.reader.name, 'question', 'which branch?')
    line = reader.read(kit.reader.name, kit.root)
    assert '1 waiting for you (1 question; from ' + kit.sender.name in line
    assert 'A direct question is waiting on you' in line
    assert reader.read(kit.sender.name, kit.root) == ''


def test_hook_session_is_the_launchers_agent_without_environment_fallback(kit, monkeypatch):
    kit.s.post(kit.reader.name, 'question', 'which branch?')
    kit.r.post(kit.sender.name, 'status', 'working')
    monkeypatch.setenv('POOLHOUSE_WORKSPACE_AGENT', kit.sender.name)
    monkeypatch.setenv('CODEX_THREAD_ID', 'unrelated-shell')
    assert 'question' in reader.read(kit.reader.name, kit.root)
    assert 'question' not in reader.read(kit.sender.name, kit.root)


def test_a_session_with_no_token_of_its_own_never_reads_another_inbox(kit):
    kit.s.post(kit.lead.name, 'question', 'for the lead')
    with pytest.raises(Denied, match='no usable token for child-thread') as refused:
        reader.read('child-thread', kit.root)
    assert not isinstance(refused.value, harness_session.BoardUnavailable)


def test_the_token_of_another_session_under_the_agents_name_never_reads(kit):
    kit.s.post(kit.sender.name, 'question', 'for the sender')
    (kit.node.state / 'client' / kit.node.board / f'{kit.reader.name}.token').write_text(kit.sender.token + '\n')
    with pytest.raises(Denied, match='launcher-bound identity'):
        reader.read(kit.reader.name, kit.root)


def test_a_retired_session_never_reads(kit):
    kit.r.retire()
    with pytest.raises(Denied, match='refused the token'):
        reader.read(kit.reader.name, kit.root)


def test_a_checkout_of_another_project_is_never_read_with_the_session_of_this_one(kit, monkeypatch, tmp_path):
    monkeypatch.delenv('POOLHOUSE_BOARD')
    elsewhere = tmp_path / 'elsewhere'
    git.run(['init', str(elsewhere)])
    with pytest.raises(Denied, match=f'no usable token for {kit.reader.name}') as refused:
        reader.read(kit.reader.name, elsewhere)
    assert not isinstance(refused.value, harness_session.BoardUnavailable)


def test_a_read_asks_who_the_token_is_once_and_what_waits_once(kit, monkeypatch):
    kit.s.post(kit.reader.name, 'question', 'which branch?')
    calls, real = [], Client.call
    monkeypatch.setattr(Client, 'call', lambda self, method, *args, **options: calls.append(method) or real(self, method, *args, **options))
    reader.read(kit.reader.name, kit.root)
    assert calls == ['whoami', 'read']


def test_nudge_subprocess_is_bounded_and_session_explicit(monkeypatch):
    captured = []

    def run(command, **kwargs):
        captured.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout='one unread', stderr='')
    monkeypatch.setattr(harnesshook, '_reader_run', run)
    rail = harnesshook.Rail('read-only', 'codex', ['/fixture'])
    assert harnesshook.nudge('codex', rail) == 'one unread'
    command, options = captured[0]
    assert command[-3:] == ['poolhouse.workspace.notification_reader', 'codex', '/fixture'] and command[-4] == '-m'
    assert options['timeout'] == harnesshook.NUDGE_S


def test_unbound_notification_does_not_block_completed_tools(kit, monkeypatch, capsys):
    monkeypatch.setenv('PYTHONPATH', SRC)
    out = io.StringIO()
    assert harnesshook.run(['post', '--agent', 'child-thread', '--root', str(kit.root)],
                           io.StringIO('{"session_id":"child-thread"}'), out) == 0
    assert 'no usable token for child-thread' in capsys.readouterr().err
    assert 'no usable token for child-thread' in json.loads(out.getvalue())['hookSpecificOutput']['additionalContext']


def test_the_hook_post_event_carries_the_unread_line_of_the_launchers_agent(kit, monkeypatch):
    monkeypatch.setenv('PYTHONPATH', SRC)
    kit.s.post(kit.reader.name, 'question', 'which branch?')
    out = io.StringIO()
    assert harnesshook.run(['post', '--agent', kit.reader.name, '--root', str(kit.root)], io.StringIO('{}'), out) == 0
    context = json.loads(out.getvalue())['hookSpecificOutput']['additionalContext']
    assert context.startswith('workspace (data from other agents): ') and 'A direct question is waiting on you' in context


def test_explicit_parent_hook_preserves_launcher_root(monkeypatch):
    captured = []
    monkeypatch.setattr(harnesshook, 'nudge', lambda label, rail: captured.append((label, rail.roots)) or '')
    assert harnesshook.run(['post', '--agent', 'project-parent', '--root', '/launcher-root'],
                           io.StringIO('{"cwd":"/unrelated"}'), io.StringIO()) == 0
    assert captured == [('project-parent', ['/launcher-root'])]

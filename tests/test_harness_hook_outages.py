"""Hook outage diagnostics and completion admission."""
import io
import json
from types import SimpleNamespace

import pytest

from poolhouse import coding_policy, harness_claims, harnesshook
from poolhouse.board.client import Client
from poolhouse.errors import Denied
from poolhouse.interventions import Call
from poolhouse.workspace import harness_session, worktree_lifecycle


def unavailable(*args, **kwargs):
    raise harness_session.BoardUnavailable('board unavailable: connection refused')


def test_post_outage_does_not_reverse_a_completed_tool(monkeypatch, capsys):
    monkeypatch.setattr(harnesshook, '_reader_run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout='', stderr='connection refused'))
    out = io.StringIO()
    assert harnesshook.run(['post', '--agent', 'worker', '--root', '/project'], io.StringIO('{}'), out) == 0
    context = json.loads(out.getvalue())['hookSpecificOutput']['additionalContext']
    assert 'connection refused' in context
    assert 'connection refused' in capsys.readouterr().err


def test_stop_is_not_blocked_when_the_board_is_unavailable(monkeypatch):
    monkeypatch.setattr(Client, 'call', lambda *args, **kwargs: pytest.fail('completion asked the node'))
    assert harnesshook.stop(harnesshook.Rail(coding_policy.Policy('ask-dangerous'), 'worker', ['/project'])) == {}


def test_stop_does_not_run_explicit_cleanup_admission(monkeypatch):
    def refused(*args, **kwargs):
        raise Denied('completion refused')
    monkeypatch.setattr(worktree_lifecycle, 'require_clean', refused)
    result = harnesshook.stop(harnesshook.Rail(coding_policy.Policy('ask-dangerous'), 'worker', ['/project']))
    assert result == {}


def test_pre_does_not_consult_workspace_ownership(monkeypatch):
    payload = {'tool_name': 'Write', 'tool_input': {'file_path': '/project/test.py'}, 'cwd': '/project'}
    rail = harnesshook.Rail(coding_policy.Policy('ask-dangerous'), 'worker', ['/project'])
    def forbidden(*args, **kwargs):
        pytest.fail('ordinary writes must not query or reserve workspace ownership')
    monkeypatch.setattr(harness_claims, 'conflict', forbidden)
    monkeypatch.setattr(harness_claims, 'reserve', forbidden)
    answer = harnesshook.pre(payload, rail)['hookSpecificOutput']
    assert answer['permissionDecision'] == 'allow'
    assert 'ownership' not in answer['permissionDecisionReason']


def test_an_ordinary_write_never_contacts_the_node(monkeypatch, tmp_path):
    def down(*args, **kwargs):
        pytest.fail('ordinary write contacted the node')
    monkeypatch.setattr(Client, 'call', down)
    payload = {'tool_name': 'Write', 'tool_input': {'file_path': str(tmp_path / 'test.py')}, 'cwd': str(tmp_path)}
    answer = harnesshook.pre(payload, harnesshook.Rail(coding_policy.Policy('ask-dangerous'), 'worker', [str(tmp_path)]))['hookSpecificOutput']
    assert answer['permissionDecision'] == 'allow'
    assert 'ownership' not in answer['permissionDecisionReason']


@pytest.mark.parametrize('secret', ['poolhouse-ws1.worker.' + 'a' * 43, 'poolhouse-ws1.worker.one/child.two.' + 'b' * 43,
                                   'poolhouse-sk1.' + 'c' * 43, 'sk-' + 'a' * 30,
                                   'https://owner:private-password@example.invalid',
                                   '-----BEGIN PRIVATE KEY-----hidden-----END PRIVATE KEY-----'])
def test_diagnostic_redacts_credentials(secret, monkeypatch):
    monkeypatch.setenv('POOLHOUSE_TEST_TOKEN', 'opaque-local-credential')
    result = harnesshook._diagnostic(Denied('cannot connect ' + secret + ' opaque-local-credential'))
    assert secret not in result and 'opaque-local-credential' not in result
    assert 'cannot connect' in result
    assert len(harnesshook._diagnostic(Denied('x' * 2000))) < 1100


@pytest.mark.parametrize('role', ['read-only', 'ask-changes', 'ask-dangerous'])
def test_launcher_keeps_normal_role_policy_without_admission(monkeypatch, tmp_path, role):
    def no_board_session(*args, **kwargs):
        pytest.fail('resource-free launcher requested mutation authority')
    monkeypatch.setattr(harness_claims, 'session_of', no_board_session)
    monkeypatch.setattr(harnesshook, '_ask', lambda *args, **kwargs: (False, 'test declined'))
    args = {'command': 'poolhouse --no-browser'}
    rail = harnesshook.Rail(coding_policy.Policy(role), 'worker', [str(tmp_path)])
    expected = harnesshook.decide(coding_policy.Policy(role), Call('Bash', args), roots=rail.roots)
    result = harnesshook.pre({'tool_name': 'Bash', 'tool_input': args, 'cwd': str(tmp_path)}, rail)
    action = result['hookSpecificOutput']['permissionDecision']
    assert action == ('allow' if expected.action == 'allow' else 'deny')


def test_a_board_outage_never_waives_the_destructive_call_guard(monkeypatch, tmp_path):
    monkeypatch.setattr(harness_claims, 'session_of', unavailable)
    monkeypatch.setattr(harnesshook, '_ask', lambda *args, **kwargs: (False, 'test declined'))
    args = {'command': f'poolhouse --no-browser; rm -rf {tmp_path / "owned"}'}
    result = harnesshook.pre({'tool_name': 'Bash', 'tool_input': args, 'cwd': str(tmp_path)},
                            harnesshook.Rail(coding_policy.Policy('ask-dangerous'), 'worker', [str(tmp_path)]))
    assert result['hookSpecificOutput']['permissionDecision'] == 'deny'


@pytest.mark.parametrize('error', [Denied('board offline'), OSError('connection refused'),
                                  RuntimeError('daemon unavailable')])
def test_outer_post_failure_keeps_completed_tool_results(error, monkeypatch, capsys):
    def broken(_label, _rail):
        raise error
    monkeypatch.setattr(harnesshook, 'post', broken)
    assert harnesshook.run(['post'], io.StringIO('{}'), io.StringIO()) == 0
    diagnostic = capsys.readouterr().err
    assert 'notification unavailable' in diagnostic and str(error) in diagnostic


def test_unhandled_post_failure_is_nonblocking(monkeypatch, capsys):
    exits = []
    monkeypatch.setattr(harnesshook.sys, 'argv', ['harnesshook', 'post'])
    monkeypatch.setattr(harnesshook.os, '_exit', exits.append)
    harnesshook._block(Denied, Denied('daemon unavailable'), None)
    assert exits == [0]
    assert 'Denied: daemon unavailable' in capsys.readouterr().err


def test_post_checkpoint_outage_preserves_pending_message_alert(monkeypatch):
    seen = []

    def pending(label, rail=None):
        seen.append(label)
        return '1 waiting for you; run inbox\nworkspace notification unavailable: connection refused'

    monkeypatch.setattr(harnesshook, 'nudge', pending)
    out = io.StringIO()
    assert harnesshook.run(['post', '--agent', 'worker', '--root', '/project'], io.StringIO('{}'), out) == 0
    context = json.loads(out.getvalue())['hookSpecificOutput']['additionalContext']
    assert 'connection refused' in context
    assert '1 waiting for you; run inbox' in context
    assert seen == ['worker']


def test_post_lifecycle_failure_preserves_stop_message_alert(monkeypatch):
    monkeypatch.setattr(harnesshook, '_reader_run', lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stdout='urgent message waiting; run inbox',
                                        stderr='checkpoint missing shadow'))
    result = harnesshook.post('worker', harnesshook.Rail(coding_policy.Policy('ask-dangerous'), 'worker', ['/project']))
    context = result['hookSpecificOutput']['additionalContext']
    assert 'checkpoint missing shadow' in context
    assert 'urgent message waiting; run inbox' in context

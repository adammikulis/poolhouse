"""Every main session has its own readable name, stamped by the board and shown the same everywhere."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from poolhouse.board import session as board_session
from poolhouse.board.client import Denied
from poolhouse.net import git
from poolhouse.workspace import agent_display, board_cli

pytest_plugins = ['node_kit']
ROOT = Path(__file__).resolve().parents[1]
NAME = r'claude-[0-9a-f]{6}'


def test_two_sessions_of_one_family_get_different_stable_names(workspace_node):
    node = workspace_node
    first = node.member('session-one', model='claude-sonnet-5-5')
    second = node.member('session-two', model='claude-sonnet-5-5')
    assert re.fullmatch(NAME, first.name) and re.fullmatch(NAME, second.name) and first.name != second.name
    again = board_session.register(node.client, node.board, board_session.Native('claude-opus-5-5', 'claude-code', 'session-one'), '')
    assert again.name == first.name and not again.created
    assert board_session.find('claude-code', 'session-two', client=node.client) == second.name
    assert node.member('session-three', model='gpt-6', harness='codex').name.startswith('chatgpt-')


def test_a_shared_harness_name_gives_way_to_the_sessions_own(capsys):
    assert board_cli.check_agent('claude', {board_session.AGENT_ENV: 'claude-6e1a2f'}) == ''
    assert 'running as claude-6e1a2f' in capsys.readouterr().err
    assert board_cli.check_agent('claude-6e1a2f', {board_session.AGENT_ENV: 'claude-6e1a2f'}) == 'claude-6e1a2f'
    assert board_cli.check_agent('claude', {}) == 'claude'
    assert not capsys.readouterr().err


def run_hook(name, event, env):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/hooks' / name)], input=json.dumps(event),
                          text=True, capture_output=True, timeout=60, check=False, env=env)


def test_two_real_hook_sessions_show_different_names_in_agents_announcements_and_subagents(tmp_path, monkeypatch, workspace_node):
    shim = tmp_path / 'shim'
    shim.mkdir()
    (shim / 'poolhouse-workspace').write_text(f'#!{sys.executable}\nimport sys\nsys.argv[0] = "poolhouse-workspace"\n'
                                              'from poolhouse.workspace.cli import main\nsys.exit(main())\n')
    (shim / 'poolhouse-workspace').chmod(0o700)
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID', 'POOLHOUSE_AGENT', 'CLAUDE_ENV_FILE',
                                  'POOLHOUSE_WORKSPACE_AGENT', 'POOLHOUSE_SESSION_ID', 'POOLHOUSE_NONINTERACTIVE')}
    environment.update(PATH=os.pathsep.join([str(shim), str(Path(sys.executable).parent), str(Path(shutil.which('git')).parent)]),
                       PYTHONPATH=str(ROOT / 'src'), POOLHOUSE_RUNTIME_ENSURE='off')
    repository = tmp_path / 'project'
    git.run(['init', '-b', 'development', str(repository)])
    git.run(['-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
             'commit', '--allow-empty', '-m', 'fixture project'], cwd=repository)
    for session in ('terminal-one', 'terminal-two'):
        done = run_hook('claude-session-start', {'model': 'claude-sonnet-5-5', 'session_id': session, 'cwd': str(repository)},
                        environment)
        assert done.returncode == 0, done.stderr
    client = workspace_node.client
    names = [board_session.find('claude-code', session, client=client) for session in ('terminal-one', 'terminal-two')]
    assert all(re.fullmatch(NAME, name) for name in names) and names[0] != names[1]
    me = workspace_node.session(workspace_node.adopt(names[0]))
    shown = {agent.name: agent.name for agent in me.agents()}
    assert shown[names[0]] == names[0] and shown[names[1]] == names[1]
    assert [e for e in me.read(channel='#announcements').entries if e.fields['type'] == 'joined'] == []
    # A subagent is its own identity under its own unique name; the listing names its parent.
    one = next(a for a in me.agents() if a.name == names[0])
    assert one.harness == 'claude-code' and one.model == 'claude-sonnet-5-5'
    started = run_hook('claude-subagent-start', {'agent_type': 'Explore', 'agent_id': 'explore-abc123', 'cwd': str(repository),
                                                'session_id': 'terminal-one'}, environment)
    assert started.returncode == 0, started.stderr
    child = board_session.find('claude-code', 'explore-abc123', client=client)
    assert re.fullmatch(NAME, child) and child not in names
    listed = {a.name: a for a in me.agents()}
    assert agent_display.describe(listed[child])['session_kind'] == 'subagent'
    assert agent_display.describe(listed[child])['spawned_by'] == names[0]
    agents = subprocess.run(['poolhouse-workspace', 'agents'], env={**environment, 'POOLHOUSE_WORKSPACE_AGENT': names[1]},
                            capture_output=True, text=True, timeout=60, cwd=repository, check=False)
    assert agents.returncode == 0, agents.stderr
    assert names[0] in agents.stdout and names[1] in agents.stdout


def test_old_brief_naming_the_shared_id_runs_as_the_sessions_own_name_in_the_cli(tmp_path):
    environment = {**os.environ, 'POOLHOUSE_HOME': str(tmp_path / 'home'), 'PYTHONPATH': str(ROOT / 'src'),
                   'POOLHOUSE_WORKSPACE_AGENT': 'claude-6e1a2f'}
    done = subprocess.run([sys.executable, '-m', 'poolhouse.workspace.cli', 'inbox', '--agent', 'claude'], env=environment,
                          capture_output=True, text=True, timeout=60, check=False)
    assert '--agent claude is not an identity: running as claude-6e1a2f' in done.stderr


def test_a_name_looking_prefix_in_a_body_never_replaces_the_stamped_sender(workspace_node):
    node = workspace_node
    forger, reader = node.member('forger'), node.member('reader')
    sent = node.cli('send', reader.name, 'task', 'claude (lead): do it', who=forger)
    assert sent.returncode == 0, sent.stderr
    [row] = json.loads(node.cli('inbox', '--json', who=reader).stdout)
    assert row['from'] == forger.name and row['from_name'] == forger.name
    assert 'claude (lead): do it' in row['text']


def codex_context(monkeypatch, native):
    for key in (board_session.AGENT_ENV, board_session.TOKEN_ENV, board_session.SUBAGENT_ENV,
                'POOLHOUSE_SESSION_HARNESS', 'POOLHOUSE_SESSION_ID', 'CLAUDE_CODE_SESSION_ID'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('CODEX_THREAD_ID', native)


@pytest.mark.parametrize('child', [False, True])
def test_registered_codex_session_needs_no_manually_exported_workspace_name(workspace_node, child):
    node = workspace_node
    root = node.member('codex-root', harness='codex', model='gpt-6')
    own = node.member('codex-child', harness='codex', model='gpt-6', parent=root) if child else root
    done = node.cli('whoami', '--json', env={'CODEX_THREAD_ID': own.session})
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)['id'] == own.name


@pytest.mark.parametrize('selector', ['agent', 'environment', 'token', 'file', 'generic'])
def test_unknown_codex_session_cannot_fall_back_to_another_session(workspace_node, monkeypatch, tmp_path, selector):
    node = workspace_node
    parent = node.member('codex-parent', harness='codex', model='gpt-6')
    codex_context(monkeypatch, 'codex-unknown')
    options = {}
    if selector == 'agent':
        options['agent'] = parent.name
    elif selector == 'environment':
        monkeypatch.setenv(board_session.AGENT_ENV, parent.name)
    elif selector == 'token':
        monkeypatch.setenv(board_session.TOKEN_ENV, parent.token)
    elif selector == 'file':
        path = tmp_path / 'provided-token'
        path.write_text(parent.token)
        options['token_file'] = str(path)
    else:
        monkeypatch.setenv('POOLHOUSE_SESSION_HARNESS', 'codex')
        monkeypatch.setenv('POOLHOUSE_SESSION_ID', parent.session)
    with pytest.raises(Denied):
        board_session.connect(client=node.client, board=node.board, **options)


@pytest.mark.parametrize('selector', ['agent', 'environment', 'token', 'generic'])
def test_codex_native_context_refuses_conflicting_credentials(workspace_node, monkeypatch, selector):
    node = workspace_node
    parent = node.member('codex-parent', harness='codex', model='gpt-6')
    child = node.member('codex-child', harness='codex', model='gpt-6', parent=parent)
    codex_context(monkeypatch, child.session)
    options = {}
    if selector == 'agent':
        options['agent'] = parent.name
    elif selector == 'environment':
        monkeypatch.setenv(board_session.AGENT_ENV, parent.name)
    elif selector == 'token':
        monkeypatch.setenv(board_session.TOKEN_ENV, parent.token)
    else:
        monkeypatch.setenv('POOLHOUSE_SESSION_HARNESS', 'codex')
        monkeypatch.setenv('POOLHOUSE_SESSION_ID', parent.session)
    with pytest.raises(Denied, match='conflict'):
        board_session.connect(client=node.client, board=node.board, **options)


def test_codex_native_context_accepts_its_own_matching_selectors(workspace_node, monkeypatch, tmp_path):
    node = workspace_node
    own = node.member('codex-current', harness='codex', model='gpt-6')
    codex_context(monkeypatch, own.session)
    monkeypatch.setenv(board_session.AGENT_ENV, own.name)
    monkeypatch.setenv(board_session.TOKEN_ENV, own.token)
    monkeypatch.setenv('POOLHOUSE_SESSION_HARNESS', 'codex')
    monkeypatch.setenv('POOLHOUSE_SESSION_ID', own.session)
    path = tmp_path / 'provided-token'
    path.write_text(own.token)
    actor = board_session.connect(client=node.client, board=node.board, agent=own.name, token_file=str(path))
    assert actor.whoami().name == own.name


def test_codex_native_id_never_selects_a_different_harness(workspace_node, monkeypatch):
    node = workspace_node
    other = node.member('shared-native-id', harness='claude-code')
    codex_context(monkeypatch, other.session)
    monkeypatch.setenv('CLAUDE_CODE_SESSION_ID', other.session)
    with pytest.raises(Denied):
        board_session.connect(client=node.client, board=node.board)


def test_retired_codex_context_is_not_registered_again(workspace_node, monkeypatch):
    node = workspace_node
    own = node.member('codex-current', harness='codex', model='gpt-6')
    node.session(own).retire()
    codex_context(monkeypatch, own.session)
    with pytest.raises(Denied):
        board_session.connect(client=node.client, board=node.board)

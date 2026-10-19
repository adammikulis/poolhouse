"""Every main session has its own readable name, stamped by the board and shown the same everywhere."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from poolhouse.board import session as board_session
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

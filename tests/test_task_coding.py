"""Native coding uses bounded turns and cancels its owned process."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from poolhouse import isolation, pi
from poolhouse.workspace import localagent, task_coding
from tests.isolation_kit import without_a_sandbox
from tests.sandbox_kit import AbsentBackend


def owning_session(identity="bound-worker"):
    return SimpleNamespace(board="demo", call=lambda method: {"name": identity})


def manager_of(**fields):
    agent = localagent.Agent('worker', fields.pop('model', 'qwen'), identity='bound-worker', **fields)
    return task_coding.TaskManager(None, owning_session(), None, agent)


@pytest.mark.redteam
def test_native_turn_and_output_limits_preserve_authority():
    manager = manager_of(profile='coding', harness='claude', effort='high', max_effort='low')
    args = manager._arguments('claude', ['--print'])
    assert args[0] == '--print' and '--system-prompt' in args and args[args.index('--tools') + 1] == 'Read,Edit,Write,Bash,Glob,Grep,Agent'
    assert manager._arguments('pi', ['--mode', 'json']) == ['--mode', 'json']
    assert manager._extras('pi') == {}
    with pytest.raises(ValueError, match='supported coding harness'):
        manager._arguments('unrecognized', [])


@pytest.mark.parametrize('effort, thinking', [('off', False), ('medium', True)])
def test_native_template_thinking_is_separate_from_output_and_stream(effort, thinking):
    manager = manager_of(model='Qwen3.8-27B.gguf', harness='claude', effort=effort)
    extras = manager._extras('claude')
    assert json.loads(extras['CLAUDE_CODE_EXTRA_BODY']) == {'chat_template_kwargs': {'enable_thinking': thinking}}
    assert 'MAX_THINKING_TOKENS' not in extras and 'CLAUDE_CODE_MAX_OUTPUT_TOKENS' not in extras
    assert set(extras) <= isolation.allowed('claude')


def test_native_effort_switch_has_no_generated_output_or_thinking_cap():
    manager = manager_of(model='/cache/Qwen3.8-27B-UD-Q4_K_XL.gguf', harness='claude', effort='off')
    assert json.loads(manager._extras('claude')['CLAUDE_CODE_EXTRA_BODY'])['chat_template_kwargs']['enable_thinking'] is False
    manager.agent = replace(manager.agent, effort='medium')
    assert json.loads(manager._extras('claude')['CLAUDE_CODE_EXTRA_BODY'])['chat_template_kwargs']['enable_thinking'] is True


def test_proposal_binds_committed_files_and_deletions(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    git = task_coding.git
    git.run(['init', str(project)])
    git.run(['config', 'user.name', 'Test'], cwd=project)
    git.run(['config', 'user.email', 'test@example.invalid'], cwd=project)
    git.run(['config', 'core.autocrlf', 'true'], cwd=project)
    (project / 'deleted.py').write_text('OLD = True\n')
    git.run(['add', 'deleted.py'], cwd=project)
    git.run(['commit', '-m', 'baseline'], cwd=project)
    baseline = git.head(project)
    (project / 'deleted.py').unlink()
    (project / 'new.py').write_text('NEW = True\n')
    git.run(['add', '--', 'deleted.py', 'new.py'], cwd=project)
    git.run(['commit', '-m', 'task changes'], cwd=project)
    state = tmp_path / 'machine-state'
    state.mkdir(exist_ok=True)
    (state / 'late-write').write_text('test machine state')
    task = {'scope': {'baseline_commit': baseline}}
    turn = SimpleNamespace(text='Changed code and verified checks.', session='session')
    proposal = task_coding._proposal(localagent.Agent('worker', 'qwen'), task, project, turn, 'python')
    assert set(proposal['artifacts']) == {'.task.patch', '.task-report.md', 'new.py'}
    assert 'deleted file mode' in (project / '.task.patch').read_text()
    assert proposal['provenance']['commit'] == git.head(project)
    task_coding.repo.reviewed_files(project, git.head(project), proposal['artifacts'])
    assert not git.run(['status', '--porcelain'], cwd=project).stdout.strip()
    (project / 'new.py').write_text('DIRTY = True\n')
    with pytest.raises(RuntimeError, match='uncommitted changes'):
        task_coding._proposal(localagent.Agent('worker', 'qwen'), task, project, turn, 'python')


def test_saved_task_settings_keep_independent_budget_without_opening_dispatch(tmp_path, monkeypatch):
    from poolhouse.fleet.conversations import Conversations

    project = tmp_path / 'project'
    project.mkdir()
    store = Conversations(tmp_path / 'chats')
    conversation = store.start(model='qwen', settings={
        'harness': 'pi', 'project': str(project),
        'effort': 'high', 'max_effort': 'low', 'max_output_tokens': 32000})
    saved = store.get(conversation.id)
    assert saved.settings['harness'] == 'pi'
    assert saved.settings['project'] == str(project)
    assert saved.settings['max_output_tokens'] == 32000
    assert saved.settings['effort'] == 'high' and saved.settings['max_effort'] == 'low'
    assert 'mode' not in saved.settings
    with pytest.raises(ValueError, match='unknown conversation setting'):
        store.start(model='qwen', settings={'mode': 'coding'})

    from poolhouse.workspace import coding_turns
    monkeypatch.setattr(coding_turns.person, 'session', lambda project: SimpleNamespace(name='bound-worker'))
    without_a_sandbox(monkeypatch, AbsentBackend)
    # the task runs from the daemon, outside any agent; a suite an agent runs has an agent for an ancestor, which the widening gate refuses for --project
    monkeypatch.setattr(pi.harnessing, 'decided', lambda args, asked: isolation.Launch((), (), False, False))
    monkeypatch.setattr(pi.harnessing, 'binary_for', lambda _name: '/usr/bin/true')
    agent = localagent.Agent('worker', 'qwen', identity='bound-worker', profile='coding', harness='pi', max_output_tokens=32000)
    manager = task_coding.TaskManager(store, owning_session(), None, agent)
    turn = task_coding.Turn(conversation.id)
    manager._run(turn, conversation, 'Implement a queue')
    assert turn.state == 'failed' and 'exited with status 2' in turn.error
    assert any('built-in sandbox tool is missing' in str(event.get('status', '')) for event in turn.events), 'the sentence is told at once'
    assert store.get(conversation.id).settings == saved.settings
    assert list(project.iterdir()) == []


def test_unlimited_native_tool_counter_preserves_parser_bounds(tmp_path):
    from poolhouse.workspace import task_caps
    path = tmp_path / "counter.json"
    path.write_text(json.dumps({'version': 1, 'calls': 10000, 'limit': None}))
    assert task_caps.admit(path)
    assert json.loads(path.read_text())["calls"] == 10001
    path.write_text(json.dumps({'version': 1, 'calls': 0, 'limit': 2}))
    assert task_caps.admit(path) and task_caps.admit(path)
    assert not task_caps.admit(path)
    path.write_text(json.dumps({'version': 1, 'calls': True, 'limit': None}))
    with pytest.raises(ValueError):
        task_caps.admit(path)


@pytest.mark.parametrize('authenticated', ['', None, 'other-worker'])
def test_task_parent_rejects_empty_or_mismatched_authenticated_identity(authenticated):
    agent = localagent.Agent('worker', 'qwen', identity='bound-worker')
    with pytest.raises(ValueError, match='authenticated agent session'):
        task_coding.TaskManager(None, owning_session(authenticated), None, agent)


def test_task_parent_does_not_fall_back_after_authentication_refusal():
    def refused(method):
        raise PermissionError('scratch session refused')

    session = SimpleNamespace(board='demo', name='bound-worker', call=refused)
    agent = localagent.Agent('worker', 'qwen', identity='bound-worker')
    with pytest.raises(PermissionError, match='scratch session refused'):
        task_coding.TaskManager(None, session, None, agent)

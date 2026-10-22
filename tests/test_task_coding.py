"""Native coding uses bounded turns and cancels its owned process."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from poolhouse import coding_policy, harnessing
from poolhouse.workspace import localagent, task_coding


def owning_session(identity="bound-worker"):
    return SimpleNamespace(board="demo", call=lambda method: {"name": identity})


@pytest.fixture
def native(tmp_path):
    project, home = tmp_path / 'project', tmp_path / 'harness'
    project.mkdir()
    home.mkdir()
    settings = home / 'settings.json'
    command = harnessing.hook_command('pre', policy=coding_policy.Policy('read-only'),
                                     agent='bound-worker', root=project, protect=[str(home)])
    settings.write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [
        {'type': 'command', 'command': command}]}]}}))
    return ['claude', '--settings', str(settings)], (home, 'claude', 'prompt', str(project))



@pytest.mark.redteam
def test_native_turn_and_output_limits_preserve_authority(monkeypatch, native):
    agent = localagent.Agent('worker', 'qwen', identity='bound-worker', profile='coding', harness='claude',
                             effort='high', max_effort='low')
    seen = {}
    def process(self, turn, command, environment, context):
        seen.update(command=command, environment=environment)
        return 0
    monkeypatch.setattr(task_coding.Manager, '_process', process)
    manager = task_coding.TaskManager(None, owning_session(), None, agent)
    command, context = native
    manager._process(None, [*command, '--print'], {'AUTHORITY': 'unchanged'}, context)
    assert '--max-turns' not in seen['command']
    settings = json.loads(context[0].joinpath('task-settings.json').read_text())
    hooks = settings['hooks']['PreToolUse']
    assert '--protect' in hooks[0]['hooks'][0]['command']
    assert 'poolhouse.workspace.task_caps' in hooks[1]['hooks'][0]['command']
    assert seen['command'][seen['command'].index('--system-prompt') + 1] == task_coding.BOOTSTRAP
    assert seen['command'][seen['command'].index('--tools') + 1] == 'Read,Edit,Write,Bash,Glob,Grep,Agent'
    assert '--dangerously-skip-permissions' not in seen['command']
    assert 'CLAUDE_CODE_MAX_OUTPUT_TOKENS' not in seen['environment']
    assert seen['environment']['CLAUDE_CODE_EFFORT_LEVEL'] == 'low'
    assert seen['environment']['AUTHORITY'] == 'unchanged'
    manager._process(None, ['pi', '--mode', 'json'], {'AUTHORITY': 'unchanged'},
                     (None, 'pi', 'prompt', None))
    assert seen['command'] == ['pi', '--mode', 'json']
    assert seen['environment']['AUTHORITY'] == 'unchanged'


@pytest.mark.parametrize('effort, thinking', [('off', False), ('medium', True)])
def test_native_template_thinking_is_separate_from_output_and_stream(monkeypatch, native, effort, thinking):
    agent = localagent.Agent('worker', 'Qwen3.8-27B.gguf', identity='bound-worker', harness='claude', effort=effort)
    seen = {}
    monkeypatch.setattr(task_coding.Manager, '_process',
                        lambda self, turn, command, environment, context: seen.update(environment) or 0)
    manager = task_coding.TaskManager(None, owning_session(), None, agent)
    command, context = native
    manager._process(None, command, {'CLAUDE_CODE_MAX_OUTPUT_TOKENS': '12000',
        'CLAUDE_CODE_EXTRA_BODY': json.dumps({'stream': True, 'max_tokens': 12000,
            'chat_template_kwargs': {'custom': 'kept', 'enable_thinking': not thinking}})},
        context)
    body = json.loads(seen['CLAUDE_CODE_EXTRA_BODY'])
    assert body == {'stream': True, 'max_tokens': 12000,
                    'chat_template_kwargs': {'custom': 'kept', 'enable_thinking': thinking}}
    assert seen['CLAUDE_CODE_MAX_OUTPUT_TOKENS'] == '12000'
    assert 'MAX_THINKING_TOKENS' not in seen


def test_native_effort_switch_has_no_generated_output_or_thinking_cap(monkeypatch, native):
    agent = localagent.Agent('worker', '/cache/Qwen3.8-27B-UD-Q4_K_XL.gguf', identity='bound-worker', harness='claude', effort='off')
    seen = {}
    monkeypatch.setattr(task_coding.Manager, '_process',
                        lambda self, turn, command, environment, context: seen.update(environment) or 0)
    manager = task_coding.TaskManager(None, owning_session(), None, agent)
    command, context = native
    manager._process(None, command, {}, context)
    assert json.loads(seen['CLAUDE_CODE_EXTRA_BODY'])['chat_template_kwargs']['enable_thinking'] is False
    assert 'MAX_THINKING_TOKENS' not in seen and 'CLAUDE_CODE_MAX_OUTPUT_TOKENS' not in seen
    manager.agent = replace(agent, effort='medium')
    manager._process(None, command, seen.copy(), context)
    assert json.loads(seen['CLAUDE_CODE_EXTRA_BODY'])['chat_template_kwargs']['enable_thinking'] is True
    manager._process(None, command, {'MAX_THINKING_TOKENS': '0'}, context)
    assert seen['MAX_THINKING_TOKENS'] == '0'
    assert json.loads(seen['CLAUDE_CODE_EXTRA_BODY'])['chat_template_kwargs']['enable_thinking'] is False


@pytest.mark.parametrize('body', ['broken', '[]', '{"chat_template_kwargs":[]}'])
def test_invalid_native_extra_body_does_not_launch(monkeypatch, native, body):
    monkeypatch.setattr(task_coding.Manager, '_process', lambda *_: pytest.fail('native launched'))
    manager = task_coding.TaskManager(None, owning_session(), None, localagent.Agent('worker', 'qwen', identity='bound-worker'))
    command, context = native
    with pytest.raises(ValueError):
        manager._process(None, command, {'CLAUDE_CODE_EXTRA_BODY': body}, context)


@pytest.mark.parametrize('stopped', [False, True])
def test_task_refuses_before_conversation_or_cancellation_work(tmp_path, monkeypatch, stopped):
    def forbidden(*args, **kwargs):
        pytest.fail('Closed task dispatch reached a side effect')
    for owner, name in ((task_coding, 'Conversations'), (task_coding.la, 'Status'),
                        (task_coding.work_reputation, 'brief_on'), (task_coding.Turn, 'cancel')):
        monkeypatch.setattr(owner, name, forbidden)
    task = {'id': 'task:' + 'a' * 32, 'title': 'Fix queue', 'description': 'Fix the queue',
            'acceptance': ['Fails correctly']}
    for _ in range(2):
        with pytest.raises(ValueError, match='cannot enforce isolated session permissions'):
            task_coding.perform(owning_session(), tmp_path,
                                localagent.Agent('worker', 'qwen', identity='bound-worker'), task,
                                task_coding.Run(tmp_path, lambda: stopped, forbidden))
    assert list(tmp_path.iterdir()) == []


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
    from poolhouse import coding
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

    def forbidden(*args, **kwargs):
        pytest.fail('Closed task dispatch reached a native or person operation')
    monkeypatch.setattr(coding, 'launch_coding_agent', forbidden)
    from poolhouse.workspace import coding_turns
    monkeypatch.setattr(coding_turns.person, 'session', forbidden)
    agent = localagent.Agent('worker', 'qwen', identity='bound-worker', profile='coding', harness='pi', max_output_tokens=32000)
    manager = task_coding.TaskManager(store, owning_session(), None, agent)
    turn = task_coding.Turn(conversation.id)
    for _ in range(2):
        with pytest.raises(ValueError, match='cannot enforce isolated session permissions'):
            manager._run(turn, conversation, 'Implement a queue')
    assert store.get(conversation.id).settings == saved.settings
    assert store.get(conversation.id).messages == []
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


def test_task_turn_refuses_before_creating_saved_state_or_dispatch(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Closed task turn reached saved state or dispatch')
    monkeypatch.setattr(task_coding.la, 'Status', forbidden)
    monkeypatch.setattr(task_coding, 'Conversations', forbidden)
    monkeypatch.setattr(task_coding.TaskManager, '_run', forbidden)
    monkeypatch.setattr(task_coding, '_proposal', forbidden)
    task = {'id': 'task:' + 'b' * 32, 'title': 'Inspect queue', 'description': 'Inspect the queue',
            'acceptance': ['Returns a report']}
    agent = localagent.Agent('worker', 'qwen', identity='bound-worker', harness='pi', max_output_tokens=32000)
    for _ in range(2):
        with pytest.raises(ValueError, match='cannot enforce isolated session permissions'):
            task_coding.perform(owning_session(), tmp_path / 'state', agent, task,
                                task_coding.Run(tmp_path, lambda: False, forbidden))
    assert list(tmp_path.iterdir()) == []


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


def test_task_launch_refuses_classifier_substitution_before_native_process(native, monkeypatch):
    import shlex
    import sys

    command, context = native
    settings = json.loads(context[0].joinpath('settings.json').read_text())
    settings['hooks']['PreToolUse'][0]['hooks'][0]['command'] = shlex.join([
        sys.executable, '-c', "print('poolhouse.harnesshook pre')", '--protect', str(context[0])])
    context[0].joinpath('settings.json').write_text(json.dumps(settings))
    monkeypatch.setattr(task_coding.Manager, '_process', lambda *_args: pytest.fail('native launched'))
    manager = task_coding.TaskManager(None, owning_session(), None,
                                    localagent.Agent('worker', 'qwen', identity='bound-worker'))
    with pytest.raises(ValueError, match='maintained classifier hook'):
        manager._process(None, command, {}, context)

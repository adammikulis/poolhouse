"""Native coding uses bounded turns and cancels its owned process."""

import json
import threading
from dataclasses import replace
from pathlib import Path
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


def test_stop_cancels_native_turn(tmp_path, monkeypatch):
    stop = threading.Event()
    cancelled = []
    conversation = SimpleNamespace(id='conversation')
    monkeypatch.setattr(task_coding, 'Conversations', lambda *_: SimpleNamespace(start=lambda **_: conversation))
    monkeypatch.setattr(task_coding.git, 'head', lambda _: 'a' * 40)
    monkeypatch.setattr(task_coding.la, 'folder', lambda _: tmp_path)
    monkeypatch.setattr(task_coding.work_reputation, 'brief_on', lambda *_: 'Recorded reputation data')
    monkeypatch.setattr(task_coding.Turn, 'cancel', lambda self: (cancelled.append(True), self.cancelled.set()))
    def run(self, turn, conversation, prompt):
        assert 'calling done' not in prompt
        assert 'Linux testing is on hold' in prompt
        assert 'Recorded reputation data' not in prompt
        stop.set()
        assert turn.cancelled.wait(1)
    monkeypatch.setattr(task_coding.TaskManager, '_run', run)
    task = {'id': 'task:' + 'a' * 32, 'title': 'Fix queue', 'description': 'Fix the queue', 'acceptance': ['Fails correctly']}
    with pytest.raises(RuntimeError, match='cancelled'):
        task_coding.perform(owning_session(), tmp_path, localagent.Agent('worker', 'qwen', identity='bound-worker'),
                            task, task_coding.Run(tmp_path, stop.is_set, lambda _: None))
    assert cancelled == [True]


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


def test_pi_launcher_receives_independent_budget_and_clamped_effort(tmp_path, monkeypatch):
    from poolhouse import coding
    from poolhouse.fleet.conversations import Conversations

    project = tmp_path / 'project'
    project.mkdir()
    store = Conversations(tmp_path / 'chats')
    conversation = store.start(model='qwen', settings={
        'harness': 'pi', 'project': str(project),
        'effort': 'high', 'max_effort': 'low', 'max_output_tokens': 32000})
    seen = {}
    def launch(model, policy, project, **options):
        seen.update(options, model=model, policy=policy, project=project)
        return 0
    monkeypatch.setattr(coding, 'launch_coding_agent', launch)
    agent = localagent.Agent('worker', 'qwen', identity='bound-worker', profile='coding', harness='pi', max_output_tokens=32000)
    manager = task_coding.TaskManager(store, owning_session(), None, agent)
    from poolhouse.workspace import coding_turns
    monkeypatch.setattr(coding_turns.person, 'session', lambda *_: pytest.fail('agent cannot borrow a person session'))
    turn = task_coding.Turn(conversation.id)
    manager._run(turn, conversation, 'Implement a queue')
    assert turn.state == 'completed' and not turn.error
    assert seen['parent'] == agent.identity == 'bound-worker'
    assert seen['model'] == 'qwen' and seen['project'] == str(project)
    assert seen['name'] != agent.identity
    assert 'session_factory' not in seen
    assert seen['max_output_tokens'] == 32000
    assert seen['effort'] == 'low'
    assert seen['max_turns'] is None
    protected, = seen['protected_paths']
    assert Path(protected).is_dir()
    assert Path(protected).parent == store.root / 'harness' / conversation.id


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


@pytest.mark.parametrize("role,permissions", [("read-only", "read-only"), ("approve-first", "ask-changes"),
                                             ("plan-and-go", "ask-dangerous")])
def test_task_turn_creates_current_saved_conversation_before_dispatch(tmp_path, monkeypatch, role, permissions):
    seen = {}
    monkeypatch.setattr(task_coding.git, 'head', lambda _: 'a' * 40)
    monkeypatch.setattr(task_coding.work_reputation, 'brief_on', lambda *_: 'Recorded reputation data')

    def run(manager, turn, conversation, prompt):
        saved = manager.store.get(conversation.id)
        assert saved is not None
        assert 'mode' not in saved.settings
        assert saved.settings['harness'] == 'pi'
        assert saved.settings['project'] == str(tmp_path)
        assert saved.settings['max_output_tokens'] == 32000
        assert saved.settings['role'] == role
        assert saved.settings['permissions'] == permissions and saved.settings['execution_mode'] == 'execute'
        seen['conversation'] = saved.id
        turn.text = 'Reviewed task result'

    monkeypatch.setattr(task_coding.TaskManager, '_run', run)
    monkeypatch.setattr(task_coding, '_proposal', lambda *args: {'summary': args[3].text})
    task = {'id': 'task:' + 'b' * 32, 'title': 'Inspect queue', 'description': 'Inspect the queue',
            'acceptance': ['Returns a report']}
    agent = localagent.Agent('worker', 'qwen', identity='bound-worker', harness='pi', max_output_tokens=32000, role=role)
    result = task_coding.perform(owning_session(), tmp_path / 'state', agent, task,
                                task_coding.Run(tmp_path, lambda: False, lambda _: None))
    assert seen['conversation']
    assert result == {'summary': 'Reviewed task result'}


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

"""Existing Codex registrations select task management without manual identity arguments."""

import os
import subprocess

import pytest
from harness_kit import checkouts

from poolhouse import node_launch, person
from poolhouse.board import client, place, session
from poolhouse.workspace import cli, localcli

pytest_plugins = ['node_kit']


@pytest.fixture
def native(workspace_node, tmp_path, monkeypatch):
    node = workspace_node
    for key in (*person.AGENT_MARKERS, session.AGENT_ENV, session.TOKEN_ENV, session.SUBAGENT_ENV,
                'POOLHOUSE_SESSION_ID', 'POOLHOUSE_SESSION_HARNESS', 'CLAUDE_CODE_SESSION_ID', 'POOLHOUSE_BOARD'):
        monkeypatch.delenv(key, raising=False)
    project = checkouts(tmp_path).primary
    node.board = place.resolve(node.client, project)
    actor = node.member('native-cli-owner', harness='codex')
    monkeypatch.chdir(project)
    monkeypatch.setenv('CODEX_THREAD_ID', actor.session)
    monkeypatch.setattr(node_launch, 'default_state', lambda: node.state)
    monkeypatch.setattr(node.client, 'autostart', False)
    monkeypatch.setattr(node_launch, 'ensure_node', lambda *_a, **_k: pytest.fail('started node'))
    return node, actor


def args(action, *extra):
    return cli.COMMANDS.parser().parse_args(['agent', action, 'existing-worker', '--task', 'existing-task', *extra])


@pytest.mark.parametrize('action', ['schedule', 'start', 'stop'])
def test_native_task_dispatch_derives_own_identity_and_reuses_its_session(native, monkeypatch, action, tmp_path):
    _node, actor = native
    assert not os.environ.get(session.AGENT_ENV)
    seen = []
    resolved = []
    connect = localcli._session

    def once(options):
        caller = connect(options)
        resolved.append(caller)
        return caller

    monkeypatch.setattr(localcli, '_session', once)

    def verified(caller):
        assert caller.whoami().name == actor.name
        assert caller.client.autostart is False
        seen.append(caller)
        return 0

    monkeypatch.setattr(localcli.authority, 'require', lambda *_a, **_k: pytest.fail('wrong person/global path'))
    monkeypatch.setattr(localcli.person, 'on', lambda *_a, **_k: pytest.fail('person fallback'))
    monkeypatch.setattr(localcli.task_scheduler, 'watch', lambda caller, *_a, **_k: verified(caller))
    monkeypatch.setattr(localcli, '_start', lambda _a, caller, _b: verified(caller))
    monkeypatch.setattr(localcli, '_stop', lambda _a, _b, caller=None: verified(caller))
    assert localcli.run(args(action), tmp_path) == 0
    assert len(seen) == 1 and seen == resolved


@pytest.mark.parametrize('conflict', ['unknown', 'retired', 'explicit', 'inherited'])
def test_native_task_stop_refuses_bad_context_before_any_dispatch_or_person_fallback(native, monkeypatch, conflict, tmp_path):
    node, actor = native
    extra = []
    if conflict == 'unknown':
        monkeypatch.setenv('CODEX_THREAD_ID', 'unknown-native')
    elif conflict == 'retired':
        node.session(actor).retire()
    else:
        other = node.member('another-cli-owner', harness='codex')
        if conflict == 'explicit':
            extra = ['--agent', other.name]
        else:
            monkeypatch.setenv(session.AGENT_ENV, other.name)
    monkeypatch.setattr(localcli, '_stop', lambda *_a, **_k: pytest.fail('stopped without own identity'))
    monkeypatch.setattr(localcli.person, 'on', lambda *_a, **_k: pytest.fail('person fallback'))
    with pytest.raises(client.Denied):
        localcli.run(args('stop', *extra), tmp_path)


def test_native_task_management_does_not_register_an_unknown_project(native, monkeypatch, tmp_path):
    node, _actor = native
    unknown = tmp_path / 'unregistered'
    unknown.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'unregistered', str(unknown)], check=True)
    monkeypatch.chdir(unknown)
    original = client.Client.call

    def no_registration(self, method, *a, **kw):
        assert method not in ('register', 'project_add')
        return original(self, method, *a, **kw)

    monkeypatch.setattr(client.Client, 'call', no_registration)
    with pytest.raises(client.Denied):
        localcli.run(args('start'), tmp_path)
    assert node.session(_actor).whoami().name == _actor.name


def test_list_remains_local_without_a_project_or_registration(native, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('CODEX_THREAD_ID', 'unknown-native')
    monkeypatch.setattr(localcli, 'Client', lambda *_a, **_k: pytest.fail('list needed node'))
    monkeypatch.setattr(localcli, '_list', lambda _base: 0)
    assert localcli.run(args('list'), tmp_path) == 0


def test_native_caller_cannot_create_a_new_worker_without_an_existing_task(native, monkeypatch, tmp_path):
    options = args('start')
    options.task = ''
    monkeypatch.setattr(localcli.localmodel, 'choose', lambda *_a, **_k: pytest.fail('selected a new model'))
    with pytest.raises(ValueError, match='existing worker name and --task'):
        localcli.run(options, tmp_path)


def test_native_child_cannot_stop_its_parent_without_existing_task_authority(native, monkeypatch, tmp_path):
    from poolhouse.board.tasks import Tasks
    from poolhouse.errors import Denied
    from poolhouse.workspace import localagent

    node, parent = native
    child = node.member('native-cli-child', harness='codex', parent=parent)
    runner = localagent.Agent('parent-worker', 'unused-model', identity=parent.name, board=node.board, project=str(localcli.Path.cwd()))
    localagent.save(tmp_path, runner)
    task = Tasks(node.session(parent)).create({'title': 'Existing task', 'description': 'Check task ownership',
                                               'acceptance': ['Ownership preserved'], 'assignees': [parent.name]})
    monkeypatch.setenv('CODEX_THREAD_ID', child.session)
    monkeypatch.setattr(localcli.ls, 'stop', lambda *_a, **_k: pytest.fail('stopped parent'))
    options = args('stop')
    options.target, options.task = runner.name, task['id']
    with pytest.raises(Denied, match='creator of a task'):
        localcli.run(options, tmp_path)

"""Authorization and launcher connections do not create projects before refusing a source."""

from functools import partial
from types import SimpleNamespace

import pytest
from harness_kit import checkouts
from test_board_connection_existing import known as _known

from poolhouse import harness_claims, harnessid
from poolhouse.board import client, person
from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.workspace import harness_session, localagent, localcli, localstart, task_authority

pytest_plugins = ['node_kit']
known = _known


@pytest.mark.parametrize('path', ['comparison', 'launcher-auth', 'invite', 'person', 'person-cli', 'approved-roots', 'task-source'])
def test_unknown_project_authorization_or_connection_never_creates_project_or_placer(known, tmp_path, monkeypatch, path):
    node, actor, project = known
    unknown = checkouts(tmp_path / 'unknown').primary
    s = node.session(actor)
    call = None
    if path == 'comparison':
        call = partial(localstart._check_board, s, str(unknown))
    elif path == 'launcher-auth':
        call = partial(harness_session._authenticated, actor.name, unknown)
    elif path == 'invite':
        call = partial(harnessid.invite, 'requested-child', unknown, actor.name, lambda _text: None)
    elif path == 'person':
        call = partial(person.session, unknown, client=node.client)
    elif path == 'person-cli':
        monkeypatch.delenv('CODEX_THREAD_ID')
        call = partial(localcli._session, SimpleNamespace(agent='', token_file='', board='', project=str(unknown)))
    elif path == 'approved-roots':
        call = partial(harness_claims.reserve, 'Write', {'file_path': str(project / 'new.py')},
                                               str(project), actor.name, [str(project), str(unknown)])
    else:
        worker = node.member('task-source-worker', harness='codex', parent=actor)
        base = tmp_path / 'metadata'
        localagent.save(base, localagent.Agent('task-source-worker', 'unused-model', identity=worker.name,
                                                  board=node.board, project=str(unknown)))
        task = Tasks(s).create({'title': 'Existing task', 'description': 'Compare source ownership',
                               'acceptance': ['Source preserved'], 'assignees': [worker.name]})
        call = partial(task_authority.authorize, s, base, worker.name, task['id'])
    before_projects = s.call('project_list')
    before_names = {who.name for who in s.agents(True)}
    mutations = []
    original = client.Client.call

    def observed(self, method, *args, **kwargs):
        if method in ('register', 'register_person', 'project_add'):
            mutations.append(method)
        return original(self, method, *args, **kwargs)

    monkeypatch.setattr(client.Client, 'call', observed)
    refused = False
    try:
        call()
    except (client.NodeError, OSError, ValueError, Denied):
        refused = True
    assert s.call('project_list') == before_projects
    assert {who.name for who in s.agents(True)} == before_names
    assert mutations == [] and refused


def test_known_source_comparison_and_launcher_authentication_keep_the_same_identity(known):
    node, actor, project = known
    before = node.session(actor).call('project_list')
    names = {who.name for who in node.session(actor).agents(True)}
    assert localstart._check_board(node.session(actor), str(project)) is None
    connected, name = harness_session._authenticated(actor.name, project)
    assert name == actor.name and connected.whoami().name == actor.name
    assert connected.board == node.board
    assert node.session(actor).call('project_list') == before
    assert {who.name for who in node.session(actor).agents(True)} == names

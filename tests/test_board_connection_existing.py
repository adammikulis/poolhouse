"""Connecting and looking up a session never initialize a project as a hidden prerequisite."""

import pytest
from harness_kit import checkouts

from poolhouse import node_launch, person
from poolhouse.board import client, place, public, session

pytest_plugins = ['node_kit']


@pytest.fixture
def known(workspace_node, tmp_path, monkeypatch):
    node = workspace_node
    for key in (*person.AGENT_MARKERS, session.AGENT_ENV, session.TOKEN_ENV, session.SUBAGENT_ENV,
                'POOLHOUSE_SESSION_ID', 'POOLHOUSE_SESSION_HARNESS', 'CLAUDE_CODE_SESSION_ID', place.BOARD_ENV):
        monkeypatch.delenv(key, raising=False)
    project = checkouts(tmp_path / 'known').primary
    node.board = place.resolve(node.client, project)
    actor = node.member('existing-native', harness='codex')
    monkeypatch.setenv('CODEX_THREAD_ID', actor.session)
    monkeypatch.setattr(node_launch, 'default_state', lambda: node.state)
    monkeypatch.chdir(project)
    return node, actor, project


@pytest.mark.parametrize('operation', ['find', 'session-connect', 'public-connect', 'public-register'])
def test_unknown_repository_lookup_or_connection_does_not_create_a_project_or_session(known, tmp_path, monkeypatch, operation):
    node, actor, _project = known
    unknown = checkouts(tmp_path / 'unknown').primary
    before_projects = node.session(actor).call('project_list')
    before_agents = node.session(actor).agents(True)
    mutations = []
    original = client.Client.call

    def observed(self, method, *a, **kw):
        if method in ('register', 'project_add'):
            mutations.append(method)
        return original(self, method, *a, **kw)

    monkeypatch.setattr(client.Client, 'call', observed)
    if operation == 'find':
        assert session.find('codex', actor.session, cwd=unknown, client=node.client) == ''
    else:
        raised = False
        call = {'session-connect': session.connect, 'public-connect': public.connect,
                'public-register': public.register}[operation]
        try:
            call(cwd=unknown)
        except client.Denied:
            raised = True
    assert node.session(actor).call('project_list') == before_projects
    assert node.session(actor).agents(True) == before_agents
    assert mutations == []
    if operation != 'find':
        assert raised


def test_known_project_infers_its_native_session_without_creating_anything(known):
    node, actor, project = known
    before_projects = node.session(actor).call('project_list')
    before_names = {who.name for who in node.session(actor).agents(True)}
    assert session.connect().whoami().name == actor.name
    assert public.connect().name == actor.name
    assert session.find('codex', actor.session) == actor.name
    assert place.existing(node.client, project) == node.board
    assert node.session(actor).call('project_list') == before_projects
    assert {who.name for who in node.session(actor).agents(True)} == before_names


def test_explicit_board_and_environment_locate_existing_identity_without_creating_project(known, tmp_path, monkeypatch):
    node, actor, _project = known
    unknown = checkouts(tmp_path / 'elsewhere').primary
    before = node.session(actor).call('project_list')
    assert public.connect(board=node.board, cwd=unknown).name == actor.name
    monkeypatch.setenv(place.BOARD_ENV, node.board)
    assert session.find('codex', actor.session, cwd=unknown) == actor.name
    assert session.connect(cwd=unknown).name == actor.name
    assert node.session(actor).call('project_list') == before


def test_explicit_script_registration_adds_one_session_but_no_project(known):
    node, actor, _project = known
    before_projects = node.session(actor).call('project_list')
    before_names = {who.name for who in node.session(actor).agents(True)}
    script = public.register('deliberate-script')
    assert script.id == node.board and script.name not in before_names
    assert public.register('deliberate-script').name == script.name
    assert {who.name for who in node.session(actor).agents(True)} == before_names | {script.name}
    assert node.session(actor).call('project_list') == before_projects


@pytest.mark.parametrize('bad', ['name', 'native'])
def test_existing_resolution_does_not_bypass_native_identity_checks(known, monkeypatch, bad):
    node, _actor, _project = known
    options = {}
    if bad == 'native':
        monkeypatch.setenv('CODEX_THREAD_ID', 'unknown-native')
    else:
        options['agent'] = node.member('different-native', harness='codex').name
    before = node.session(_actor).call('project_list')
    with pytest.raises(client.Denied):
        public.connect(**options)
    assert node.session(_actor).call('project_list') == before

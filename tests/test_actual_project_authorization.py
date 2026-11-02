"""A board selector cannot attest that an execution path belongs to that board."""

import pytest
from harness_kit import checkouts
from test_board_connection_existing import known as _known

from poolhouse.board import client, place
from poolhouse.workspace import localstart

pytest_plugins = ['node_kit']
known = _known


def test_injected_board_cannot_authorize_an_unknown_actual_path(known, monkeypatch, tmp_path):
    node, actor, _project = known
    unknown = checkouts(tmp_path / 'unmapped').primary
    monkeypatch.setenv(place.BOARD_ENV, node.board)
    before = node.session(actor).call('project_list')
    with pytest.raises(client.Denied):
        localstart._check_board(node.session(actor), str(unknown))
    assert node.session(actor).call('project_list') == before


def test_actual_path_mapping_ignores_selectors_but_connection_selection_keeps_them(known, monkeypatch, tmp_path):
    node, actor, project = known
    foreign = checkouts(tmp_path / 'foreign').primary
    foreign_board = place.resolve(node.client, foreign)
    monkeypatch.setenv(place.BOARD_ENV, foreign_board)
    assert place.at_path(node.client, project) == node.board
    assert localstart._check_board(node.session(actor), str(project)) is None
    assert place.existing(node.client, project) == foreign_board
    assert place.existing(node.client, foreign, named=node.board) == node.board
    monkeypatch.setenv(place.BOARD_ENV, node.board)
    assert place.at_path(node.client, foreign) == foreign_board
    with pytest.raises(ValueError, match='another board'):
        localstart._check_board(node.session(actor), str(foreign))


@pytest.mark.parametrize('target', ['unknown', 'foreign'])
@pytest.mark.parametrize('guard', ['roots', 'task-source', 'invite', 'saved-actor'])
def test_board_locator_cannot_bypass_execution_project_guards(known, monkeypatch, tmp_path, target, guard):
    from functools import partial

    from poolhouse import harness_claims, harnessid
    from poolhouse.board.tasks import Tasks
    from poolhouse.errors import Denied
    from poolhouse.workspace import harness_session, localagent, task_authority

    node, actor, project = known
    path = checkouts(tmp_path / target).primary
    if target == 'foreign':
        assert place.resolve(node.client, path) != node.board
    s = node.session(actor)
    if guard == 'roots':
        call = partial(harness_claims.reserve, 'Write', {'file_path': str(project / 'new.py')},
                       str(project), actor.name, [str(project), str(path)])
    elif guard == 'task-source':
        worker = node.member('source-worker', harness='codex', parent=actor)
        base = tmp_path / 'metadata'
        localagent.save(base, localagent.Agent('source-worker', 'unused-model', identity=worker.name,
                                               board=node.board, project=str(path)))
        task = Tasks(s).create({'title': 'Check source', 'description': 'Preserve project ownership',
                               'acceptance': ['Correct project'], 'assignees': [worker.name]})
        call = partial(task_authority.authorize, s, base, worker.name, task['id'])
    elif guard == 'invite':
        call = partial(harnessid.invite, 'requested-child', path, actor.name, lambda _text: None)
    else:
        call = partial(harness_session._authenticated, actor.name, path)
    before_projects = s.call('project_list')
    before_names = {who.name for who in s.agents(True)}
    original = client.Client.call

    def no_mutation(self, method, *args, **kwargs):
        assert method not in ('register', 'register_person', 'project_add', 'reserve')
        return original(self, method, *args, **kwargs)

    monkeypatch.setenv(place.BOARD_ENV, node.board)
    monkeypatch.setattr(client.Client, 'call', no_mutation)
    with pytest.raises((client.NodeError, Denied, ValueError)):
        call()
    assert s.call('project_list') == before_projects
    assert {who.name for who in s.agents(True)} == before_names

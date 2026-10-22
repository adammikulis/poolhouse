"""Readiness is advisory; current local credentials and registered folders remain required."""
import json

import pytest
from launch_support import browser, signed
from test_fleet_ui import Serving

from poolhouse import home
from poolhouse.fleet.conversations import Conversations
from poolhouse.fleet.projects import ProjectRegistry
from poolhouse.fleet.setup_projects import Projects
from poolhouse.workspace import coding_routes


@pytest.fixture
def catalogue_api(tmp_path, monkeypatch):
    monkeypatch.setattr(home, 'user_home', lambda: tmp_path.resolve())
    monkeypatch.setattr(coding_routes.hub, 'discover', lambda **_options: [])
    monkeypatch.setattr(coding_routes.harnessing, 'binary_for', lambda _name: 'fixture-installed')
    app = signed(Serving(tmp_path / 'device'))
    app.ui.root = tmp_path / 'state'
    app.ui.root.mkdir(mode=0o700)
    app.ui.projects = ProjectRegistry(app.ui.root, 'fixture-device')
    app.ui.conversations = Conversations(app.ui.root / 'chats')
    try:
        yield app
    finally:
        app.close()


def get(app, **options):
    return app.call('/ui/coding/catalogue', headers=browser(app), **options)


def test_actual_closed_floor_is_not_opened_by_installed_harnesses(catalogue_api):
    app = catalogue_api
    status, first, _ = get(app)
    assert status == 200
    assert first['can_start'] is False
    assert first['enforcement_error'] == 'This coding runtime cannot enforce isolated session permissions'
    assert all(row['available'] for row in first['harnesses'])
    assert first['selected_project'] is None
    assert get(app)[1] == first
    assert not app.ui.projects.boards() and not app.ui.conversations.all() and not app.runner.jobs


def test_selected_folder_uses_current_registered_identity_without_paths(catalogue_api):
    app = catalogue_api
    folder = app.ui.root.parent / 'chosen'
    folder.mkdir(mode=0o700)
    projects = Projects(app.ui.root, app.ui.projects)
    projects.select(folder, 'a' * 32, current=lambda: True)
    project = projects.create('a' * 32, current=lambda: True)
    app.ui.projects.select_coding(project['id'], 'b' * 32, 0, current=lambda: True)
    status, data, _ = get(app)
    assert status == 200 and data['selected_project'] == project
    assert set(data['selected_project']) == {'id', 'title'}
    assert str(folder) not in json.dumps(data['selected_project'])
    assert data['can_start'] is False


def test_constructor_without_project_registry_is_honestly_unselected(catalogue_api):
    app = catalogue_api
    app.ui.projects = None
    status, data, _ = get(app)
    assert status == 200 and data['selected_project'] is None and data['can_start'] is False


@pytest.mark.parametrize('when', ['during', 'after'])
def test_revoked_session_cannot_return_folder_or_catalogue(catalogue_api, monkeypatch, when):
    app = catalogue_api
    original = app.ui.projects.coding_project
    def revoke(*, current):
        if when == 'during':
            app.ui.sessions.close(app.cookie.split('=', 1)[1])
        result = original(current=current)
        if when == 'after':
            app.ui.sessions.close(app.cookie.split('=', 1)[1])
        return result
    monkeypatch.setattr(app.ui.projects, 'coding_project', revoke)
    status, data, _ = get(app)
    assert status == 403 and 'selected_project' not in data and 'models' not in data


@pytest.mark.parametrize('error', [FileNotFoundError('/private/fixture/missing'), ValueError('/private/fixture/protected')])
def test_invalid_selected_folder_is_never_replaced_or_exposed(catalogue_api, monkeypatch, error):
    def unavailable(**_options):
        raise error
    monkeypatch.setattr(catalogue_api.ui.projects, 'coding_project', unavailable)
    status, data, _ = get(catalogue_api)
    assert status == 400
    assert data == {'error': 'The selected coding folder is unavailable. Choose it again.'}


def test_foreign_origin_and_noncredentialed_sessions_cannot_discover(catalogue_api):
    app = catalogue_api
    status, data, _ = app.call('/ui/coding/catalogue', headers={'Origin': 'https://foreign.invalid'})
    assert status == 403 and 'models' not in data
    session = app.ui.sessions.open('fixture-view', 'pool-key')
    status, data, _ = get(app, cookie=app.ui.sessions.cookie_header(session).split(';', 1)[0])
    assert status == 403 and 'selected_project' not in data

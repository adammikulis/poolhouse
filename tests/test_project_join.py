"""The node takes a project of the app only when its signed-in person joins it, and only the folder they chose or shared."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from browser_expect import expect

from poolhouse.board import person, place
from poolhouse.board.tasks import Tasks
from poolhouse.fleet import extension_routes
from poolhouse.fleet.projects import ProjectRegistry, identity
from poolhouse.workspace import fleet_routes, task_routes

pytest_plugins = ['node_kit', 'person_kit']

READS = ('tasks', 'board/boards', 'board/head', 'board/wait')


def git(root, *args):
    subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', *args],
                   check=True, capture_output=True)


def repository(root, note):
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'README.md').write_text(note)
    git(root, 'add', 'README.md')
    git(root, 'commit', '-q', '-m', note)
    return root


@pytest.fixture
def app(project_tasks, tmp_path):
    """The routes in front of the app's own project registry and a node that holds none of its projects."""
    call, node, _ = project_tasks
    registry = ProjectRegistry(tmp_path / 'registry', 'local')
    call.ui.projects = registry

    def ask(ident, suffix='tasks', **options):
        if suffix == 'board/wait':
            options.setdefault('path', f'/ui/projects/{ident}/board/wait?after=0&timeout=0')
        return call(suffix, project=ident, **options)

    def join(ident, **options):
        return ask(ident, 'board/connect', **{'method': 'POST', 'body': {}, **options})

    def choose(root):
        ident = identity(root)
        registry.register(root, ident)
        return ident

    def held():
        return {row['id']: [source['path'] for source in row['sources']] for row in node.client.call('project_list')}

    return SimpleNamespace(node=node, registry=registry, tmp=tmp_path, ask=ask, join=join, choose=choose, held=held)


@pytest.mark.parametrize('suffix', READS)
def test_reading_a_chosen_project_registers_nothing_and_offers_to_join(app, suffix):
    ident = app.choose(repository(app.tmp / 'chosen', 'chosen'))
    before = app.held()
    status, body = app.ask(ident, suffix)
    assert status == 403 and body['person_setup_required'] is True, body
    assert app.held() == before


def test_joining_registers_the_chosen_project_and_its_pages_answer(app):
    root = repository(app.tmp / 'chosen', 'chosen')
    ident = app.choose(root)
    status, body = app.join(ident)
    assert status == 200 and body['connected'] is True, body
    assert [str(root)] in app.held().values()
    for suffix in READS:
        assert app.ask(ident, suffix)[0] == 200, suffix
    status, made = app.ask(ident, method='POST', body={'spec': {'title': 'First', 'acceptance': ['Done']}})
    assert status == 200, made
    session = person.session(root, app.node.client)
    assert [task['id'] for task in Tasks(session).list()['tasks']] == [made['id']]


def test_a_removed_project_stays_removed_until_its_person_joins_again(app):
    root = repository(app.tmp / 'chosen', 'chosen')
    ident = app.choose(root)
    assert app.join(ident)[0] == 200
    board = place.at_path(app.node.client, root)
    place.remove(app.node.client, str(root))
    for suffix in READS:
        status, body = app.ask(ident, suffix)
        assert status == 403 and body['person_setup_required'] is True, (suffix, body)
        assert board not in app.held(), suffix
    assert app.join(ident)[0] == 200 and board in app.held()


@pytest.mark.parametrize(('suffix', 'options', 'status'), [
    ('tasks', {'method': 'POST', 'raw': b'[1]'}, 403),
    ('tasks', {'method': 'POST', 'body': {'spec': {'title': 'No', 'acceptance': ['No']}}}, 403),
    ('board/connect', {'method': 'POST', 'body': {'name': 'other'}}, 400),
    ('board/connect', {'method': 'POST', 'raw': b'[1]'}, 400),
    ('board/connect', {}, 405),
    ('board/bogus', {}, 403),
    ('board/post', {'method': 'POST', 'body': {'to': '#general', 'body': 'No'}}, 403),
    ('board/connect', {'method': 'POST', 'body': {}, 'cookie': 'forged'}, 403),
    ('board/connect', {'method': 'POST', 'body': {}, 'origin': 'https://outside.invalid'}, 403),
    ('board/connect', {'method': 'POST', 'body': {}, 'ip': '192.0.2.7'}, 403),
])
def test_only_the_accepted_join_registers_anything(app, suffix, options, status):
    ident = app.choose(repository(app.tmp / 'chosen', 'chosen'))
    before = app.held()
    got, body = app.ask(ident, suffix, **options)
    assert got == status, body
    assert app.held() == before


def test_a_folder_the_app_only_found_is_not_registered_until_it_is_shared(app, project_tasks):
    root = repository(app.tmp / 'found', 'found by the app')
    registry = ProjectRegistry(app.tmp / 'found-registry', 'local', (root,))
    project_tasks[0].ui.projects = registry
    ident = registry.boards()[0]['id']
    before = app.held()
    for status, body in (app.ask(ident), app.join(ident)):
        assert status == 403 and 'Share' in body['error'] and 'person_setup_required' not in body, body
    assert app.held() == before
    registry.share(ident)
    assert app.join(ident)[0] == 200
    assert [str(root)] in app.held().values()


def replace_with_link(app, root, victim):
    shutil.rmtree(root)
    root.symlink_to(victim)


def lose_git_directory(app, root, victim):
    shutil.rmtree(root / '.git')   # `root` sits inside the repository `outer`


def point_at_repository(app, root, victim):
    shutil.rmtree(root / '.git')
    (root / '.git').write_text(f'gitdir: {victim / ".git"}\n')


def point_at_anothers_worktree(app, root, victim):
    git(victim, 'worktree', 'add', '-q', str(app.tmp / 'victim-tree'))
    shutil.rmtree(root / '.git')
    (root / '.git').write_text(f'gitdir: {victim / ".git" / "worktrees" / "victim-tree"}\n')


def borrow_common_directory(app, root, victim):
    (root / '.git' / 'commondir').write_text(f'{victim / ".git"}\n')


def forge_identity(app, root, victim):
    ident = identity(root)
    shutil.rmtree(root)
    repository(root, 'somebody else')
    (root / '.poolhouse-project.json').write_text(json.dumps({'kind': 'project-checkout', 'project_id': ident}))


def open_to_other_users(app, root, victim):
    root.chmod(0o777)


@pytest.mark.parametrize('change', [replace_with_link, lose_git_directory, point_at_repository, point_at_anothers_worktree,
                                    borrow_common_directory, forge_identity, open_to_other_users])
def test_a_chosen_folder_that_changed_is_neither_registered_nor_read_as_another_project(app, change):
    victim = repository(app.tmp / 'victim', 'never chosen')
    other = app.choose(victim)
    assert app.join(other)[0] == 200
    assert app.ask(other, method='POST', body={'spec': {'title': 'Other secret', 'acceptance': ['x']}})[0] == 200
    outer = repository(app.tmp / 'outer', 'outer, never chosen')
    root = repository(outer / 'inner', 'chosen')
    ident = app.choose(root)
    change(app, root, victim)
    before = app.held()
    status, body = app.join(ident)
    assert status == 403 and 'person_setup_required' not in body, body
    status, body = app.ask(ident)
    assert status == 403 and 'tasks' not in body and 'person_setup_required' not in body, body
    assert app.held() == before


def test_a_linked_worktree_joins_the_repository_it_belongs_to(app):
    main = repository(app.tmp / 'main', 'main')
    tree = app.tmp / 'linked'
    git(main, 'worktree', 'add', '-q', str(tree))
    ident = app.choose(tree)
    assert app.join(ident)[0] == 200
    assert [str(main)] in app.held().values()
    assert app.ask(ident)[0] == 200


@pytest.mark.slow
def test_the_tasks_page_offers_to_join_a_chosen_project_and_then_shows_it(tasks_ui, browser_owner, tmp_path, monkeypatch):
    pytest.importorskip('playwright.sync_api')
    served, node = tasks_ui
    routes = {'tasks': task_routes.route, 'board': fleet_routes.route}
    monkeypatch.setattr(extension_routes, 'entry_points', lambda **kw: [
        SimpleNamespace(name=name, load=lambda route=route: route) for name, route in routes.items()])
    root = repository(tmp_path / 'chosen-in-app', 'chosen')
    served.ui.projects = ProjectRegistry(tmp_path / 'app-registry', 'local')
    served.ui.projects.register(root, identity(root))
    served.ui.settings.setup_done = True
    with browser_owner.pages() as page:
        page.goto(f'http://127.0.0.1:{served.port}/ui/#tasks')
        join = page.locator('tasks-view').get_by_role('button', name='Join workspace as person', exact=True)
        expect(join).to_be_visible()
        assert str(root) not in str(node.client.call('project_list'))
        join.click()
        expect(page.locator('tasks-view')).to_contain_text('Updated')
        expect(join).to_have_count(0)
    assert str(root) in str(node.client.call('project_list'))


def test_the_registry_remembers_what_the_person_chose_and_tells_no_other_device(tmp_path):
    root = repository(tmp_path / 'one', 'one')
    registry = ProjectRegistry(tmp_path / 'registry', 'local', (root,))
    ident = identity(root)
    assert registry.get(ident).chosen is False
    registry.register(Path(root), ident)
    registry.share(ident)
    again = ProjectRegistry(tmp_path / 'registry', 'local')
    assert again.get(ident).chosen is True
    assert not {'root', 'chosen', 'git_id'} & set(again.list()[0])

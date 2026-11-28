"""The node takes a project of the app only when its signed-in person joins it, and only the folder they chose or shared."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from browser_expect import expect
from launch_support import signed
from test_fleet_ui import Serving

from poolhouse.board import person, place
from poolhouse.board.client import Client, Denied
from poolhouse.board.tasks import Tasks
from poolhouse.fleet import extension_routes, project_client
from poolhouse.fleet.project_source import ProjectError
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

    return SimpleNamespace(node=node, registry=registry, tmp=tmp_path.resolve(), ask=ask, join=join, choose=choose, held=held)


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


def during(monkeypatch, method, before, after=lambda: None):
    """Run ``before`` as the node is asked for ``method``, once, and ``after`` when it has answered: the real call, in a window
    somebody else's change can fall into."""
    real = Client.call

    def call(self, name, *args, **params):
        if name != method:
            return real(self, name, *args, **params)
        monkeypatch.setattr(Client, 'call', real)
        before()
        try:
            return real(self, name, *args, **params)
        finally:
            after()

    monkeypatch.setattr(Client, 'call', call)


def swaps(app, root, victim):
    """``(swap, restore)``: put a link to ``victim`` where ``root`` is, and ``root`` back."""
    aside = app.tmp / 'moved-aside'

    def swap():
        root.rename(aside)
        root.symlink_to(victim)

    def restore():
        root.unlink()
        aside.rename(root)

    return swap, restore


@pytest.mark.parametrize('comes_back', [False, True])
def test_a_folder_swapped_as_the_node_takes_it_leaves_nothing_registered(app, monkeypatch, comes_back):
    victim = repository(app.tmp / 'victim', 'never chosen')
    root = repository(app.tmp / 'chosen', 'chosen')
    ident = app.choose(root)
    swap, restore = swaps(app, root, victim)
    before = app.held()
    during(monkeypatch, 'project_add', swap, restore if comes_back else lambda: None)
    status, body = app.join(ident)
    assert status == 403 and 'connected' not in body, body
    assert app.held() == before


def test_a_folder_swapped_after_its_check_is_not_registered(app, monkeypatch):
    victim = repository(app.tmp / 'victim', 'never chosen')
    root = repository(app.tmp / 'chosen', 'chosen')
    ident = app.choose(root)
    swap, _ = swaps(app, root, victim)
    checked = app.registry.joinable

    def then_swapped(identifier):
        found = checked(identifier)
        swap()
        return found

    monkeypatch.setattr(app.registry, 'joinable', then_swapped)
    before = app.held()
    status, body = app.join(ident)
    assert status == 403, body
    assert app.held() == before


def test_a_folder_swapped_before_a_read_shows_no_other_board(app, monkeypatch):
    victim = repository(app.tmp / 'victim', 'never chosen')
    other = app.choose(victim)
    assert app.join(other)[0] == 200
    assert app.ask(other, method='POST', body={'spec': {'title': 'Other secret', 'acceptance': ['x']}})[0] == 200
    root = repository(app.tmp / 'chosen', 'chosen')
    ident = app.choose(root)
    assert app.join(ident)[0] == 200
    swap, _ = swaps(app, root, victim)
    during(monkeypatch, 'project_resolve', swap)
    status, body = app.ask(ident)
    assert status == 403 and 'tasks' not in body, body


@pytest.mark.parametrize('place_of', ['nested', 'submodule', 'foreign-worktree'])
def test_a_folder_inside_a_joined_project_neither_shows_nor_writes_its_board(app, place_of):
    outer = repository(app.tmp / 'outer', 'outer')
    outer_ident = app.choose(outer)
    assert app.join(outer_ident)[0] == 200
    assert app.ask(outer_ident, method='POST', body={'spec': {'title': 'Outer secret', 'acceptance': ['x']}})[0] == 200
    if place_of == 'nested':
        inner = repository(outer / 'vendor' / 'inner', 'inner')
    elif place_of == 'submodule':
        library = repository(app.tmp / 'library', 'library')
        git(outer, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(library), 'sub')
        inner = outer / 'sub'
    else:
        elsewhere = repository(app.tmp / 'elsewhere', 'elsewhere')
        inner = outer / 'trees' / 'elsewhere'
        git(elsewhere, 'worktree', 'add', '-q', str(inner))
    ident = app.choose(inner)
    before = app.held()
    for status, body in (app.ask(ident), app.join(ident), app.ask(ident, method='POST', body={
            'spec': {'title': 'Written from inside', 'acceptance': ['x']}})):
        assert status == 403 and str(outer) in body['error'] and 'person_setup_required' not in body, body
        assert 'tasks' not in body
    assert app.held() == before
    assert [task['title'] for task in app.ask(outer_ident)[1]['tasks']] == ['Outer secret']


def test_a_folder_around_a_joined_project_is_refused_in_plain_words(app):
    outer = repository(app.tmp / 'outer', 'outer')
    inner = repository(outer / 'inner', 'inner')
    assert app.join(app.choose(inner))[0] == 200
    ident = app.choose(outer)
    before = app.held()
    status, body = app.join(ident)
    assert status == 403 and 'workspace' in body['error'] and 'invalid' not in body['error'], body
    assert app.held() == before


def forge_split_pointer(app, root, victim):
    """A worktree layout inside ``root`` that names it back, and beside it one whose name ends in a space, which leads to ``victim``."""
    shown = root / 'fake' / '.git' / 'worktrees' / 'w'
    followed = shown.parent / 'w '
    for folder, common in ((shown, '../..'), (followed, str(victim / '.git'))):
        folder.mkdir(parents=True)
        (folder / 'commondir').write_text(common + '\n')
        (folder / 'gitdir').write_text(f'{root / ".git"}\n')
        (folder / 'HEAD').write_text('ref: refs/heads/main\n')
    shutil.rmtree(root / '.git')
    (root / '.git').write_text(f'gitdir: {followed}\n')


def test_a_git_file_is_read_as_git_reads_it(app):
    origin = 'https://example.invalid/team/shared.git'
    root = repository(app.tmp / 'chosen', 'chosen')
    victim = repository(app.tmp / 'private-clone', 'never chosen')
    for each in (root, victim):
        git(each, 'remote', 'add', 'origin', origin)
    ident = app.choose(root)
    forge_split_pointer(app, root, victim)
    before = app.held()
    for status, body in (app.ask(ident), app.join(ident)):
        assert status == 403 and 'person_setup_required' not in body, body
    assert app.held() == before


def test_a_git_selector_in_the_daemons_environment_selects_nothing(app, monkeypatch):
    root = repository(app.tmp / 'chosen', 'chosen')
    victim = repository(app.tmp / 'victim', 'never chosen')
    ident = app.choose(root)
    monkeypatch.setenv('GIT_COMMON_DIR', str(victim / '.git'))
    status, body = app.join(ident)
    assert status == 200, body
    assert list(app.held().values()).count([str(root)]) == 1 and [str(victim)] not in app.held().values()


def submodule_of(app):
    library = repository(app.tmp / 'library', 'library')
    parent = repository(app.tmp / 'parent', 'parent')
    git(parent, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(library), 'one')
    return parent / 'one'


def separate_git_directory(app):
    root = app.tmp / 'separate'
    (app.tmp / 'kept').mkdir()
    subprocess.run(['git', 'init', '-q', '--separate-git-dir', str(app.tmp / 'kept' / 'separate.git'), str(root)], check=True)
    git(root, 'commit', '-q', '--allow-empty', '-m', 'separate')
    return root


def relative_worktree(app):
    main = repository(app.tmp / 'relative-main', 'relative')
    tree = app.tmp / 'relative-tree'
    git(main, 'worktree', 'add', '-q', str(tree))
    own = main / '.git' / 'worktrees' / 'relative-tree'
    (tree / '.git').write_text(f'gitdir: {os.path.relpath(own, tree)}\n')   # what `worktree add --relative-paths` writes
    (own / 'gitdir').write_text(f'{os.path.relpath(tree / ".git", own)}\n')
    return tree


@pytest.mark.parametrize(('layout', 'registered'), [(submodule_of, 'parent/one'), (separate_git_directory, 'separate'),
                                                    (relative_worktree, 'relative-main')])
def test_a_layout_git_keeps_elsewhere_joins_its_own_folder(app, layout, registered):
    root = layout(app)
    ident = app.choose(root)
    status, body = app.join(ident)
    assert status == 200, body
    assert [str(app.tmp / registered)] in app.held().values()
    assert app.ask(ident)[0] == 200


def test_a_second_submodule_is_not_the_first_ones_project(app):
    first = submodule_of(app)
    library = app.tmp / 'library'
    git(first.parent, '-c', 'protocol.file.allow=always', 'submodule', 'add', '-q', str(library), 'two')
    assert app.join(app.choose(first))[0] == 200
    assert app.node.client.call('project_resolve', path=str(first))
    with pytest.raises(Denied):
        app.node.client.call('project_resolve', path=str(first.parent / 'two'))


def test_a_git_directory_changed_after_the_choice_is_not_registered(app):
    root = separate_git_directory(app)
    ident = app.choose(root)
    other = app.tmp / 'kept' / 'other.git'
    shutil.copytree(app.tmp / 'kept' / 'separate.git', other)
    (root / '.git').write_text(f'gitdir: {other}\n')
    before = app.held()
    status, body = app.join(ident)
    assert status == 403, body
    assert app.held() == before


def test_unsharing_takes_the_choice_back(tmp_path):
    root = repository(tmp_path / 'one', 'one')
    registry = ProjectRegistry(tmp_path / 'registry', 'local', (root,))
    ident = identity(root)
    registry.share(ident)
    registry.unshare(ident)
    assert (registry.get(ident).shared, registry.get(ident).chosen) == (False, False)
    with pytest.raises(ProjectError):
        registry.joinable(ident)


def test_sharing_needs_a_signed_in_window(tmp_path):
    root = repository(tmp_path / 'one', 'one')
    served = Serving(tmp_path / 'ui')
    try:
        served.ui.projects = registry = ProjectRegistry(tmp_path / 'registry', 'local', (root,))
        ident = identity(root)
        share = {'method': 'POST', 'body': {'action': 'share', 'candidate_id': ident}}
        status, answer, _ = served.call('/ui/projects', **share)
        assert status == 403 and registry.get(ident).shared is False, answer
        signed(served)
        status, answer, _ = served.call('/ui/projects', **share)
        assert status == 200 and registry.get(ident).shared is True, answer
    finally:
        served.close()


@pytest.mark.parametrize('signed_in', [False, True])
def test_unsharing_needs_a_signed_in_window(tmp_path, signed_in):
    root = repository(tmp_path / 'one', 'one')
    registry = ProjectRegistry(tmp_path / 'registry', 'local', (root,))
    ident = identity(root)
    registry.share(ident)
    answers = []
    request = SimpleNamespace(ui=SimpleNamespace(projects=registry, credentialed=lambda cookie: signed_in), cookie='',
                              path=f'/ui/projects/{ident}', method='PATCH', body=lambda: {'shared': False},
                              send=lambda status, body: answers.append(status))
    assert project_client.route(request)
    assert answers == [200 if signed_in else 403] and registry.get(ident).shared is not signed_in


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

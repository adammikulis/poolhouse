"""First-run coding folders remain local and require an explicit creation action."""
import pytest

from poolhouse import home
from poolhouse.fleet.projects import ProjectRegistry
from poolhouse.fleet.setup_projects import Projects
from poolhouse.net import git


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(home, 'user_home', lambda: tmp_path.resolve())
    root = tmp_path.resolve() / 'state'
    root.mkdir(mode=0o700)
    registry = ProjectRegistry(root, 'fixture-device')
    return Projects(root, registry)


def test_default_creates_one_private_unshared_registered_project(prepared):
    first = prepared.default(current=lambda: True)
    second = prepared.default(current=lambda: True)
    assert first == second
    project = prepared.registry.get(first['id'])
    assert not project.shared
    assert not prepared.registry.list()
    path = home.user_home() / 'Poolhouse Projects' / 'Workspace'
    assert project.root == str(path)
    assert path.stat().st_mode & 0o077 == 0
    assert git.run(['ls-files'], cwd=path).stdout == ''
    assert len(prepared.registry.boards()) == 1
    assert 'path' not in first and 'root' not in first


def test_select_is_inert_until_explicit_create_and_repeats_attach(prepared, tmp_path):
    path = tmp_path.resolve() / 'chosen'
    path.mkdir()
    (path / 'notes.txt').write_text('Private existing content')
    ident = 'a' * 32
    response = prepared.select(path, ident, current=lambda: True)
    assert response == prepared.select(path, ident, current=lambda: True)
    assert response['status'] == 'needs-project'
    assert not (path / '.git').exists()
    assert not prepared.registry.boards()
    result = prepared.create(ident, current=lambda: True)
    assert result == prepared.create(ident, current=lambda: True)
    assert (path / 'notes.txt').read_text() == 'Private existing content'
    assert git.run(['ls-files'], cwd=path).stdout == ''
    assert prepared.select(path, 'b' * 32, current=lambda: True)['project'] == result
    assert not prepared.registry.get(result['id']).shared


def test_replaced_selected_directory_is_refused(prepared, tmp_path):
    path = tmp_path.resolve() / 'chosen'
    path.mkdir()
    prepared.select(path, 'a' * 32, current=lambda: True)
    path.rename(tmp_path / 'previous')
    path.mkdir()
    with pytest.raises(ValueError, match='Choose an available'):
        prepared.create('a' * 32, current=lambda: True)
    assert not (path / '.git').exists()


def test_redirected_default_and_revoked_setup_cannot_create(prepared, tmp_path):
    outside = tmp_path.resolve() / 'outside'
    outside.mkdir()
    (home.user_home() / 'Poolhouse Projects').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        prepared.default(current=lambda: True)
    assert not list(outside.iterdir())
    (home.user_home() / 'Poolhouse Projects').unlink()
    with pytest.raises(PermissionError):
        prepared.default(current=lambda: False)
    assert not (home.user_home() / 'Poolhouse Projects').exists()


def test_existing_default_content_is_never_adopted_or_overwritten(prepared):
    path = home.user_home() / 'Poolhouse Projects' / 'Workspace'
    path.mkdir(mode=0o700, parents=True)
    (path / 'notes.txt').write_text('Keep this')
    with pytest.raises(ValueError, match='already contains'):
        prepared.default(current=lambda: True)
    assert (path / 'notes.txt').read_text() == 'Keep this'
    assert not (path / '.git').exists()


def test_create_checks_revocation_after_git_init(prepared, tmp_path, monkeypatch):
    path = tmp_path.resolve() / 'chosen'
    path.mkdir()
    prepared.select(path, 'a' * 32, current=lambda: True)
    real = git.run
    current = [True]
    def run(args, **kwargs):
        result = real(args, **kwargs)
        if args[0] == 'init':
            current[0] = False
        return result
    monkeypatch.setattr(git, 'run', run)
    with pytest.raises(PermissionError):
        prepared.create('a' * 32, current=lambda: current[0])
    assert not prepared.registry.boards()
    recovered = prepared.create('a' * 32, current=lambda: True)
    assert recovered['id'] in {row['id'] for row in prepared.registry.boards()}


def test_native_folder_flow_has_fixed_purpose_no_private_fields_and_no_implicit_create(prepared, tmp_path):
    from poolhouse.fleet.setup_project_picker import Picker
    principal = {'id': 'session-a', 'scope': 'device-pool-a', 'owner': True}
    current = [True]
    picker = Picker(prepared, epoch=lambda caller: 'epoch-a',
                    current=lambda caller, epoch: current[0] and caller == principal and epoch == 'epoch-a')
    request = picker.request(principal)
    assert request == picker.request(principal)
    claim = picker.claim(request['request_id'], request['nonce'])
    assert claim['purpose'] == 'coding-project' and claim['mode'] == 'folder'
    path = tmp_path.resolve() / 'chosen'
    path.mkdir()
    response = picker.complete(**{'ident': request['request_id'], 'nonce': request['nonce'],
                                 'claim': claim['claim'], 'path': str(path)})
    assert response == {'status': 'needs-project', 'purpose': 'coding-project',
                        'project': {'id': request['request_id'], 'title': 'chosen'}}
    assert not (path / '.git').exists()
    assert 'claim' not in response and str(path) not in str(response)
    with pytest.raises(PermissionError):
        picker.create({**principal, 'id': 'foreign'}, request['request_id'])
    result = picker.create(principal, request['request_id'])
    assert result['status'] == 'complete'
    assert result == picker.create(principal, request['request_id'])
    assert picker.complete(request['request_id'], request['nonce'], claim['claim'], str(path)) == {
        key: value for key, value in result.items() if key != 'selected'}
    current[0] = False
    with pytest.raises(PermissionError):
        picker.complete(request['request_id'], request['nonce'], claim['claim'], str(path))


def test_cancelled_native_selection_cannot_create_and_replay_does_not_reopen(prepared):
    from poolhouse.fleet.setup_project_picker import Picker
    principal = {'id': 'session-a', 'scope': 'device-pool-a', 'owner': True}
    picker = Picker(prepared, epoch=lambda caller: 'epoch-a', current=lambda caller, epoch: True)
    request = picker.request(principal)
    claim = picker.claim(request['request_id'], request['nonce'])
    response = picker.cancel(request['request_id'], request['nonce'], claim['claim'])
    assert response == {'status': 'cancelled', 'purpose': 'coding-project'}
    assert picker.claim(request['request_id'], request['nonce']) == response
    with pytest.raises(PermissionError):
        picker.create(principal, request['request_id'])


@pytest.fixture
def project_app(tmp_path, monkeypatch):
    monkeypatch.setattr(home, 'user_home', lambda: tmp_path.resolve())
    from test_fleet_ui import Serving
    app = Serving(tmp_path)
    app.ui.root = app.files.parent
    app.ui.projects = ProjectRegistry(app.ui.root, 'fixture-device')
    try:
        yield app
    finally:
        app.close()


def test_real_native_routes_bind_original_session_and_keep_path_private(project_app, tmp_path):
    from launch_support import browser, sign_in

    from poolhouse.fleet.launch_secret import HEADER
    from poolhouse.fleet.session import parse_cookie
    app = project_app
    cookie = sign_in(app)
    status, challenge, _ = app.call('/ui/setup/projects/pick', method='POST', body={},
                                     headers=browser(app), cookie=cookie)
    assert status == 200, challenge
    prefix = '/ui/setup/projects/native-picks/' + challenge['request_id']
    proof = {HEADER: app.ui.launch.value}
    status, claimed, _ = app.call(prefix + '/claim', method='POST', body={'nonce': challenge['nonce']}, headers=proof)
    assert status == 200 and claimed['purpose'] == 'coding-project', claimed
    folder = tmp_path.resolve() / 'user-folder'
    folder.mkdir()
    complete = {'nonce': challenge['nonce'], 'claim': claimed['claim'], 'path': str(folder)}
    status, chosen, _ = app.call(prefix + '/complete', method='POST', body=complete, headers=proof)
    assert status == 200 and chosen['status'] == 'needs-project', chosen
    assert str(folder) not in str(chosen) and 'claim' not in chosen
    assert not (folder / '.git').exists()
    foreign = sign_in(app)
    assert app.call('/ui/setup/projects/create', method='POST', headers=browser(app), cookie=foreign,
                    body={'candidate_id': chosen['project']['id']})[0] == 403
    app.ui.sessions.close(parse_cookie(cookie))
    assert app.call(prefix + '/complete', method='POST', body=complete, headers=proof)[0] == 403
    assert not app.ui.projects.boards()


def test_real_default_requires_local_credentialed_explicit_choice(project_app):
    from launch_support import browser, sign_in
    app = project_app
    cookie = sign_in(app)
    target = home.user_home() / 'Poolhouse Projects' / 'Workspace'
    assert app.call('/ui/setup/projects/default', headers=browser(app), cookie=cookie)[0] == 405
    assert app.call('/ui/setup/projects/default', method='POST', body={})[0] == 403
    assert not target.exists()
    assert app.call('/ui/setup/projects/default', method='POST', headers=browser(app), cookie=cookie,
                    body={'path': '/untrusted'})[0] == 409
    assert not target.exists()
    for _ in range(2):
        status, result, _ = app.call('/ui/setup/projects/default', method='POST', headers=browser(app), cookie=cookie, body={'operation_id':'f' * 32})
        assert status == 200 and result['ok'] is True, result
        assert str(target) not in str(result)
    assert len(app.ui.projects.boards()) == 1
    assert not app.ui.projects.list()


@pytest.mark.parametrize('stage', ['init', 'commit', 'register'])
@pytest.mark.parametrize('use_default', [True, False])
def test_interrupted_owned_creation_reconciles_exact_receipt_once(prepared, tmp_path, monkeypatch, stage, use_default):
    if use_default:
        def create():
            return prepared.default(current=lambda: True)
        path = home.user_home() / 'Poolhouse Projects' / 'Workspace'
    else:
        path = tmp_path.resolve() / 'selected'
        path.mkdir()
        prepared.select(path, 'a' * 32, current=lambda: True)
        def create():
            return prepared.create('a' * 32, current=lambda: True)
    real_run, real_register, real_commit = git.run, prepared.registry.register, git.commit_empty
    def run(args, **kwargs):
        result = real_run(args, **kwargs)
        if args[0] == stage:
            raise RuntimeError('Simulated lost acknowledgement')
        return result
    def commit(*args, **kwargs):
        real_commit(*args, **kwargs)
        raise RuntimeError('Simulated lost acknowledgement')
    def register(*args, **kwargs):
        real_register(*args, **kwargs)
        raise RuntimeError('Simulated lost acknowledgement')
    monkeypatch.setattr(git, 'run', run)
    if stage == 'register':
        monkeypatch.setattr(prepared.registry, 'register', register)
    if stage == 'commit':
        monkeypatch.setattr(git, 'commit_empty', commit)
    with pytest.raises(RuntimeError, match='lost acknowledgement'):
        create()
    monkeypatch.setattr(git, 'run', real_run)
    monkeypatch.setattr(git, 'commit_empty', real_commit)
    monkeypatch.setattr(prepared.registry, 'register', real_register)
    result = create()
    assert create() == result
    assert git.run(['rev-list', '--count', 'HEAD'], cwd=path).stdout.strip() == '1'
    assert len(prepared.registry.boards()) == 1
    assert not prepared.registry.get(result['id']).shared


def test_other_writable_default_parent_is_not_mutated(prepared):
    parent = home.user_home() / 'Poolhouse Projects'
    parent.mkdir(mode=0o777)
    parent.chmod(0o777)
    with pytest.raises(ValueError):
        prepared.default(current=lambda: True)
    assert not (parent / 'private-workspace').exists()


@pytest.mark.parametrize('stage', ['mkdir', 'marker', 'promotion'])
def test_interrupted_stage_establishment_is_reconciled_without_adopting_unknown_directory(prepared, monkeypatch, stage):
    import poolhouse.fleet.setup_projects as module
    real_mkdir, real_write, real_promote = module.tempfile.mkdtemp, module.os.write, module.promote_new
    def mkdir(*args, **kwargs):
        real_mkdir(*args, **kwargs)
        raise RuntimeError('Interrupted after stage mkdir')
    def write(descriptor, data):
        result = real_write(descriptor, data)
        if len(data) == 64:
            raise RuntimeError('Interrupted after marker')
        return result
    def promote(*args, **kwargs):
        real_promote(*args, **kwargs)
        raise RuntimeError('Interrupted after promotion')
    if stage == 'mkdir':
        monkeypatch.setattr(module.tempfile, 'mkdtemp', mkdir)
    if stage == 'marker':
        monkeypatch.setattr(module.os, 'write', write)
    if stage == 'promotion':
        monkeypatch.setattr(module, 'promote_new', promote)
    with pytest.raises(RuntimeError, match='Interrupted after'):
        prepared.default(current=lambda: True)
    monkeypatch.setattr(module.tempfile, 'mkdtemp', real_mkdir)
    monkeypatch.setattr(module.os, 'write', real_write)
    monkeypatch.setattr(module, 'promote_new', real_promote)
    result = prepared.default(current=lambda: True)
    assert prepared.default(current=lambda: True) == result
    assert len(prepared.registry.boards()) == 1


def test_foreign_git_directory_is_never_replaced_during_promotion(prepared, monkeypatch):
    import poolhouse.fleet.setup_projects as module
    real = module.promote_new
    def promote(source, target):
        target.mkdir()
        (target / 'foreign.txt').write_text('Preserve foreign content')
        return real(source, target)
    monkeypatch.setattr(module, 'promote_new', promote)
    with pytest.raises(FileExistsError):
        prepared.default(current=lambda: True)
    target = home.user_home() / 'Poolhouse Projects' / 'Workspace' / '.git' / 'foreign.txt'
    assert target.read_text() == 'Preserve foreign content'
    assert not prepared.registry.boards()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_redirected_stage_marker_never_writes_external_target(prepared, tmp_path, monkeypatch, kind):
    import os

    import poolhouse.fleet.setup_projects as module
    real = module.tempfile.mkdtemp
    external = tmp_path.resolve() / 'external-receipt'
    if kind == 'hardlink':
        external.write_text('')
        external.chmod(0o600)
    def stage(*args, **kwargs):
        result = real(*args, **kwargs)
        marker = module.Path(result) / 'poolhouse-setup-receipt'
        if kind == 'symlink':
            marker.symlink_to(external)
        else:
            os.link(external, marker)
        return result
    monkeypatch.setattr(module.tempfile, 'mkdtemp', stage)
    with pytest.raises((OSError, ValueError)):
        prepared.default(current=lambda: True)
    if kind == 'hardlink':
        assert external.read_text() == ''
    else:
        assert not external.exists()
    assert not prepared.registry.boards()


def test_generated_workspace_commit_uses_fixed_software_identity(prepared, monkeypatch):
    for variable, value in {'GIT_AUTHOR_NAME':'Ambient author agent', 'GIT_AUTHOR_EMAIL':'ambient-author@agents.invalid',
                            'GIT_COMMITTER_NAME':'Ambient commit agent', 'GIT_COMMITTER_EMAIL':'ambient-commit@agents.invalid'}.items():
        monkeypatch.setenv(variable, value)
    project = prepared.default(current=lambda: True)
    from pathlib import Path
    path = Path(prepared.registry.get(project['id']).root)
    assert git.run(['log', '-1', '--format=%an|%ae|%cn|%ce'], cwd=path).stdout.strip() == (
        'Poolhouse setup agent|setup@agents.invalid|Poolhouse setup agent|setup@agents.invalid')


def test_default_receipt_and_race_never_replace_a_newer_native_selection(project_app, tmp_path, monkeypatch):
    from launch_support import browser, sign_in
    app = project_app
    cookie = sign_in(app)
    def choose(operation):
        return app.call('/ui/setup/projects/default', method='POST', headers=browser(app),
                        cookie=cookie, body={'operation_id': operation})[0]
    assert choose('a' * 32) == 200
    instance = app.ui.setup_projects
    folder = tmp_path.resolve() / 'native-choice'
    folder.mkdir(mode=0o700)
    instance.projects.select(folder, 'b' * 32, current=lambda: True)
    native = instance.projects.create('b' * 32, current=lambda: True)
    app.ui.projects.select_coding(native['id'], 'c' * 32, 1, current=lambda: True)
    assert choose('a' * 32) == 200
    assert app.call('/ui/setup/projects/default', method='POST', headers=browser(app), cookie=cookie,
                    body={'operation_id':'a' * 32})[1]['selected'] is False
    assert app.ui.projects.coding_project(current=lambda: True).id == native['id']
    original = instance.projects.default
    def race(**kwargs):
        app.ui.projects.select_coding(native['id'], 'd' * 32, 2, current=lambda: True)
        return original(**kwargs)
    monkeypatch.setattr(instance.projects, 'default', race)
    assert choose('e' * 32) == 403
    assert app.ui.projects.coding_project(current=lambda: True).id == native['id']


def test_default_operation_keys_are_scoped_to_the_actual_session(project_app):
    from launch_support import browser, sign_in
    app = project_app
    cookies = [sign_in(app), sign_in(app)]
    for cookie in cookies:
        status, result, _ = app.call('/ui/setup/projects/default', method='POST', headers=browser(app),
                                     cookie=cookie, body={'operation_id':'a' * 32})
        assert status == 200 and result['selected'] is True
    assert app.ui.projects.coding_revision(current=lambda: True) == 2
    status, result, _ = app.call('/ui/setup/projects/default', method='POST', headers=browser(app),
                                 cookie=cookies[0], body={'operation_id':'a' * 32})
    assert status == 200 and result['selected'] is False


def test_default_uses_account_home_outside_the_actual_daemon_state_shape(tmp_path, monkeypatch):
    account = tmp_path.resolve() / 'account'
    account.mkdir(mode=0o700)
    state = account / '.poolhouse'
    root = state / 'traind'
    root.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(home, 'user_home', lambda: account)
    monkeypatch.setenv(home.ROOT_ENV, str(state))
    projects = Projects(root, ProjectRegistry(root, 'fixture-device'))
    first = projects.default(current=lambda: True)
    assert projects.default(current=lambda: True) == first
    selected = projects.registry.resolve_coding_project(first['id'], current=lambda: True)
    assert selected.root == str(account / 'Poolhouse Projects' / 'Workspace')
    assert not (state / 'Poolhouse Projects').exists()
    assert not selected.shared

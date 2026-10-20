"""A chosen coding project is a current local preference, never a resource grant."""
import pytest

from poolhouse.fleet.projects import ProjectRegistry
from poolhouse.fleet.setup_project_picker import Picker
from poolhouse.fleet.setup_projects import Projects
from poolhouse.net import git


@pytest.fixture
def choices(tmp_path):
    state = tmp_path.resolve() / 'state'
    state.mkdir(mode=0o700)
    return Projects(state, ProjectRegistry(state, 'fixture-device'))


def project(choices, tmp_path, token):
    folder = tmp_path.resolve() / token
    folder.mkdir(mode=0o700)
    choices.select(folder, token, current=lambda: True)
    return choices.create(token, current=lambda: True)


def test_chosen_project_round_trip_is_exact_and_private(choices, tmp_path):
    registry = choices.registry
    assert registry.coding_project(current=lambda: True) is None
    selected = project(choices, tmp_path, 'a' * 32)
    receipt = registry.select_coding(selected['id'], 'b' * 32, 0, current=lambda: True)
    assert receipt == registry.select_coding(selected['id'], 'b' * 32, 0, current=lambda: True)
    restored = ProjectRegistry(choices.root, 'fixture-device')
    assert restored.coding_project(current=lambda: True).id == selected['id']
    assert restored.coding_revision(current=lambda: True) == 1
    assert not restored.coding_project(current=lambda: True).shared
    assert not restored.list()


def test_historical_receipt_cannot_overwrite_newer_choice_or_change_resource(choices, tmp_path):
    registry = choices.registry
    first = project(choices, tmp_path, 'a' * 32)
    second = project(choices, tmp_path, 'b' * 32)
    registry.select_coding(first['id'], 'c' * 32, 0, current=lambda: True)
    registry.select_coding(second['id'], 'd' * 32, 1, current=lambda: True)
    assert registry.select_coding(first['id'], 'c' * 32, 0, current=lambda: True)['current'] is False
    with pytest.raises(ValueError, match='operation changed'):
        registry.select_coding(second['id'], 'c' * 32, 0, current=lambda: True)
    with pytest.raises(ValueError, match='newer coding folder'):
        registry.select_coding(first['id'], 'e' * 32, 0, current=lambda: True)
    assert registry.coding_project(current=lambda: True).id == second['id']


def test_revocation_before_or_after_write_preserves_previous_selection(choices, tmp_path, monkeypatch):
    registry = choices.registry
    chosen = project(choices, tmp_path, 'a' * 32)
    with pytest.raises(PermissionError):
        registry.select_coding(chosen['id'], 'b' * 32, 0, current=lambda: False)
    current = [True]
    save = registry._save
    def revoke():
        save()
        current[0] = False
    monkeypatch.setattr(registry, '_save', revoke)
    with pytest.raises(PermissionError):
        registry.select_coding(chosen['id'], 'b' * 32, 0, current=lambda: current[0])
    restored = ProjectRegistry(choices.root, 'fixture-device')
    assert restored.coding_project(current=lambda: True) is None
    assert restored.coding_revision(current=lambda: True) == 0


@pytest.mark.parametrize('change', ['protected', 'missing', 'git'])
def test_each_read_rechecks_actual_resource_and_never_chooses_another(choices, tmp_path, monkeypatch, change):
    registry = choices.registry
    chosen = project(choices, tmp_path, 'a' * 32)
    registry.select_coding(chosen['id'], 'b' * 32, 0, current=lambda: True)
    folder = tmp_path.resolve() / ('a' * 32)
    if change == 'protected':
        monkeypatch.setenv('POOLHOUSE_CACHE', str(folder))
    elif change == 'missing':
        folder.rename(tmp_path / 'moved')
    else:
        (folder / '.git').rename(folder / 'previous-git')
    with pytest.raises((ValueError, RuntimeError, OSError, git.GitFailed)):
        registry.coding_project(current=lambda: True)
    assert registry.coding_revision(current=lambda: True) == 1


def test_reversed_native_completion_cannot_replace_the_newer_selection(choices, tmp_path):
    first = project(choices, tmp_path, 'a' * 32)
    second = project(choices, tmp_path, 'b' * 32)
    picker = Picker(choices, epoch=lambda principal: 'epoch', current=lambda principal, epoch: True)
    requests = []
    for session in ['one', 'two']:
        request = picker.request({'id': session, 'scope': 'pool-device', 'owner': True})
        claim = picker.claim(request['request_id'], request['nonce'])
        requests.append((request, claim))
    def complete(index, token):
        request, claim = requests[index]
        return picker.complete(request['request_id'], request['nonce'], claim['claim'], str(tmp_path.resolve() / token))
    assert complete(1, 'b' * 32)['project'] == second
    with pytest.raises(PermissionError, match='setup session ended'):
        complete(0, 'a' * 32)
    assert choices.registry.coding_project(current=lambda: True).id == second['id']
    assert first['id'] != second['id']


def test_distinct_empty_projects_have_distinct_identity_at_the_same_timestamp(choices, tmp_path, monkeypatch):
    monkeypatch.setenv('GIT_AUTHOR_DATE', '2001-01-01T00:00:00Z')
    monkeypatch.setenv('GIT_COMMITTER_DATE', '2001-01-01T00:00:00Z')
    first = project(choices, tmp_path, 'a' * 32)
    second = project(choices, tmp_path, 'b' * 32)
    assert first['id'] != second['id']
    assert len(choices.registry.boards()) == 2


def test_protected_candidate_is_refused_before_git_or_mutation(choices, tmp_path, monkeypatch):
    folder = tmp_path.resolve() / 'protected'
    folder.mkdir()
    monkeypatch.setenv('POOLHOUSE_CACHE', str(folder))
    with pytest.raises(RuntimeError, match='protected'):
        choices.select(folder, 'a' * 32, current=lambda: True)
    assert not (folder / '.git').exists()
    assert choices._read('a' * 32) is None


def test_protected_policy_is_rechecked_before_candidate_initialization(choices, tmp_path, monkeypatch):
    folder = tmp_path.resolve() / 'selected'
    folder.mkdir()
    choices.select(folder, 'a' * 32, current=lambda: True)
    monkeypatch.setenv('POOLHOUSE_CACHE', str(folder))
    with pytest.raises(RuntimeError, match='protected'):
        choices.create('a' * 32, current=lambda: True)
    assert not (folder / '.git').exists()


def test_changed_initial_commit_footer_cannot_be_reconciled(choices, tmp_path, monkeypatch):
    folder = tmp_path.resolve() / 'selected'
    folder.mkdir()
    choices.select(folder, 'a' * 32, current=lambda: True)
    commit = git.commit_empty
    def altered(cwd, identity, message):
        commit(cwd, identity, message.replace('a' * 32, 'b' * 32))
    monkeypatch.setattr(git, 'commit_empty', altered)
    with pytest.raises(ValueError, match='folder changed'):
        choices.create('a' * 32, current=lambda: True)
    monkeypatch.setattr(git, 'commit_empty', commit)
    with pytest.raises(ValueError, match='folder changed'):
        choices.create('a' * 32, current=lambda: True)
    assert git.run(['rev-list', '--count', 'HEAD'], cwd=folder).stdout.strip() == '1'
    assert not choices.registry.boards()


def test_stale_explicit_create_has_no_git_side_effect_and_old_native_receipt_is_inert(choices, tmp_path):
    principal = {'id':'session', 'scope':'pool-device', 'owner':True}
    picker = Picker(choices, epoch=lambda caller:'epoch', current=lambda caller, epoch:True)
    request = picker.request(principal)
    claim = picker.claim(request['request_id'], request['nonce'])
    folder = tmp_path.resolve() / 'pending'
    folder.mkdir()
    picker.complete(request['request_id'], request['nonce'], claim['claim'], str(folder))
    newer = project(choices, tmp_path, 'a' * 32)
    choices.registry.select_coding(newer['id'], 'b' * 32, 0, current=lambda:True)
    with pytest.raises(PermissionError):
        picker.create(principal, request['request_id'])
    assert not (folder / '.git').exists()
    assert choices.registry.coding_project(current=lambda:True).id == newer['id']
    # Complete a fresh choice, then supersede it and replay its still-valid native receipt.
    next_request = picker.request(principal)
    next_claim = picker.claim(next_request['request_id'], next_request['nonce'])
    path = str(tmp_path.resolve() / ('a' * 32))
    picker.complete(next_request['request_id'], next_request['nonce'], next_claim['claim'], path)
    choices.registry.select_coding(newer['id'], 'c' * 32, 2, current=lambda:True)
    assert picker.complete(next_request['request_id'], next_request['nonce'], next_claim['claim'], path) == {
        'status':'cancelled', 'purpose':'coding-project'}

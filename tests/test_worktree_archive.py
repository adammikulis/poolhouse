"""Obsolete automatic trial merges never replace useful source ancestry."""

import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_trees import commit, git, make_repo, tree

from poolhouse import runtime_git, trees
from poolhouse.errors import Denied
from poolhouse.workspace import cli, task_cli, worktree_archive, worktree_lifecycle as lifecycle


def build(tmp_path, manual=False):
    """A closed trial whose only unpublished commit is a merge; ``manual`` adds a change the merge did not need."""
    primary = make_repo(tmp_path)
    checkout = tree(primary, 'trial', 'worker', time.time())
    git(primary, 'checkout', '-qb', 'feature')
    commit(primary, 'feature')
    feature = git(primary, 'rev-parse', 'HEAD')
    git(primary, 'checkout', 'dev')
    commit(primary, 'advance')
    if manual:
        git(checkout, 'merge', '--no-ff', '--no-commit', 'feature')
        (checkout / 'extra').write_text('a change no automatic merge makes')
        git(checkout, 'add', 'extra')
        git(checkout, 'commit', '-qm', 'merge feature', '--no-verify')
    else:
        git(checkout, 'merge', '--no-ff', '--no-edit', 'feature')
    head = git(checkout, 'rev-parse', 'HEAD')
    git(primary, 'merge', '--no-edit', 'feature')
    published = git(primary, 'rev-parse', 'HEAD')
    git(primary, 'update-ref', 'refs/remotes/origin/dev', published)
    base = tmp_path / 'state'
    base.mkdir()
    lifecycle.remember(base, 'worker', str(checkout))
    bundle = tmp_path / 'complete.bundle'
    git(primary, 'bundle', 'create', str(bundle), 'refs/heads/trial')
    trees.close(primary, checkout, ('bundle', ''), time.time(), 'worker')
    return SimpleNamespace(primary=primary, checkout=checkout, base=base, bundle=bundle,
                           head=head, feature=feature, owner='worker', tmp=tmp_path)


@pytest.fixture
def trial(tmp_path):
    return build(tmp_path)


def archive(kit, owner='worker', claim='worker', bundle=None):
    return lifecycle.archive_obsolete(kit.base, owner, str(kit.checkout),
                                      str(bundle or kit.bundle), lambda _: claim)


def test_verified_obsolete_trial_has_separate_disposition_and_retains_history(trial):
    assert lifecycle.pending(trial.base, trial.owner)
    result = archive(trial)
    assert result['disposition'] == 'archived-obsolete-trial'
    assert result['commits'] == [trial.head]
    assert git(trial.primary, 'rev-parse', 'trial') == trial.head
    assert lifecycle.pending(trial.base, trial.owner) == []
    assert not lifecycle.scopes(trial.base, trial.owner)[0].get('cleanup')


@pytest.mark.parametrize('owner,claim', [('foreign', 'foreign'), ('worker', ''), ('worker', 'foreign')])
def test_disposition_requires_own_scope_and_live_exact_claim(trial, owner, claim):
    with pytest.raises(Denied):
        archive(trial, owner, claim)
    assert lifecycle.pending(trial.base, trial.owner)


def test_useful_nonmerge_history_must_be_published(trial):
    git(trial.primary, 'update-ref', 'refs/remotes/origin/dev', trial.feature + '^')
    with pytest.raises(Denied):
        archive(trial)


@pytest.mark.parametrize('change', ['bundle', 'ref', 'path', 'scope'])
def test_archived_proof_is_revalidated_before_completion(trial, change):
    archive(trial)
    if change == 'bundle':
        trial.bundle.write_bytes(trial.bundle.read_bytes()[:-8])
    elif change == 'ref':
        git(trial.primary, 'update-ref', 'refs/heads/trial', trial.feature)
    elif change == 'path':
        trial.checkout.mkdir()
    else:
        lifecycle._edit(trial.base, lambda rows: next(iter(rows.values()))['commits'].append(trial.feature))
    assert lifecycle.pending(trial.base, trial.owner)


def test_incremental_bundle_is_not_complete_preservation(trial, tmp_path):
    thin = tmp_path / 'thin.bundle'
    git(trial.primary, 'bundle', 'create', str(thin), 'dev..trial')
    with pytest.raises(Denied):
        archive(trial, bundle=thin)


def test_missing_maintained_bundle_close_cannot_be_fabricated_by_archive_request(trial):
    trees.update(trial.primary, lambda data: data.update(history=[]))
    with pytest.raises(Denied):
        archive(trial)


def refuse(kit, match=None, **kwargs):
    """The request is denied, no disposition is recorded and the scope still blocks completion."""
    with pytest.raises(Denied, match=match):
        archive(kit, **kwargs)
    assert 'archive' not in lifecycle.scopes(kit.base, kit.owner)[0]
    assert lifecycle.pending(kit.base, kit.owner)


def bundle_of(kit, name, *refs):
    path = kit.tmp / name
    git(kit.primary, 'bundle', 'create', str(path), *refs)
    return path


def forged_branch_bundle(kit, branch, extra=False):
    """A complete bundle of ``branch`` cut from the trial head in a clone, one commit past it when ``extra``."""
    clone = kit.tmp / (branch + '-clone')
    git(kit.tmp, 'clone', '-q', str(kit.primary), str(clone))
    git(clone, 'checkout', '-q', '-b', branch, 'origin/trial')
    if extra:
        commit(clone, 'forged')
    path = kit.tmp / (branch + '.bundle')
    git(clone, 'bundle', 'create', str(path), 'refs/heads/' + branch)
    return path


@pytest.mark.parametrize('content', [
    b'', b'not a bundle', b'# v2 git bundle\n' + b'a' * 70000,
    b'# v2 git bundle\n-' + b'a' * 40 + b' prerequisite\n\n'])
def test_malformed_bundle_is_refused(trial, content):
    trial.bundle.write_bytes(content)
    refuse(trial)


def test_truncated_bundle_is_refused(trial):
    trial.bundle.write_bytes(trial.bundle.read_bytes()[:-8])
    refuse(trial)


def test_version_three_bundle_is_refused(trial):
    path = trial.tmp / 'v3.bundle'
    git(trial.primary, 'bundle', 'create', '--version=3', str(path), 'refs/heads/trial')
    refuse(trial, 'version 2', bundle=path)


def test_bundle_over_the_limit_is_refused_and_one_at_the_limit_is_read(trial, monkeypatch):
    size = trial.bundle.stat().st_size
    monkeypatch.setattr(worktree_archive, 'MAXIMUM_BUNDLE', size - 1)
    refuse(trial, 'owned regular canonical file')
    monkeypatch.setattr(worktree_archive, 'MAXIMUM_BUNDLE', size)
    assert archive(trial)['disposition'] == 'archived-obsolete-trial'


def test_sparse_file_past_the_real_limit_is_refused_before_any_byte_is_taken(tmp_path):
    path = tmp_path / 'big.bundle'
    with path.open('wb') as stream:
        stream.truncate(worktree_archive.MAXIMUM_BUNDLE + 1)
    with pytest.raises(Denied, match='owned regular canonical file'):
        worktree_archive._snapshot(path, lambda chunk: pytest.fail('took bytes of an oversize file'))


@pytest.mark.parametrize('kind', ['fifo', 'directory'])
def test_snapshot_takes_only_a_regular_file(tmp_path, kind):
    path = tmp_path / 'special'
    if kind == 'fifo':
        os.mkfifo(path)
    else:
        path.mkdir()
    with pytest.raises(Denied, match='owned regular canonical file'):
        worktree_archive._snapshot(path, lambda chunk: pytest.fail('took bytes of a special file'))


def test_file_modified_while_it_is_read_is_refused(tmp_path, monkeypatch):
    path = tmp_path / 'changing.bundle'
    path.write_bytes(b'x' * 100)
    monkeypatch.setattr(worktree_archive, 'CHUNK', 10)
    seen = []

    def modify(chunk):
        seen.append(chunk)
        if len(seen) == 1:
            with path.open('ab') as stream:
                stream.write(b'y')

    with pytest.raises(Denied, match='changed while it was read'):
        worktree_archive._snapshot(path, modify)


def test_header_with_a_prerequisite_is_refused():
    with pytest.raises(Denied, match='prerequisites'):
        worktree_archive._check_header(b'# v2 git bundle\n-' + b'a' * 40 + b' note\n\n')


def test_open_follows_no_link_and_takes_no_relative_or_parent_component(trial):
    link, linked, real = trial.tmp / 'link.bundle', trial.tmp / 'linked', trial.tmp / 'real'
    link.symlink_to(trial.bundle)
    linked.symlink_to(trial.tmp)
    real.mkdir()
    for path in (link, linked / 'complete.bundle'):
        with pytest.raises(OSError):
            worktree_archive._open(path)
    for path in (Path('complete.bundle'), real / '..' / 'complete.bundle'):
        with pytest.raises(Denied):
            worktree_archive._open(path)


def test_bundle_that_grows_while_read_is_cut_off_at_the_limit(tmp_path, monkeypatch):
    path = tmp_path / 'growing.bundle'
    path.write_bytes(b'x' * 100)
    monkeypatch.setattr(worktree_archive, 'MAXIMUM_BUNDLE', 100)
    monkeypatch.setattr(worktree_archive, 'CHUNK', 10)
    seen = []

    def grow(chunk):
        seen.append(len(chunk))
        if len(seen) == 1:
            with path.open('ab') as stream:
                stream.write(b'x' * 100)

    with pytest.raises(Denied, match='owned regular canonical file'):
        worktree_archive._snapshot(path, grow)
    assert sum(seen) <= 100


def test_bundle_owned_by_another_user_is_refused(trial, monkeypatch):
    own = os.getuid
    monkeypatch.setattr(os, 'getuid', lambda: own() + 1)
    refuse(trial, 'owned regular canonical file')


def test_checkout_that_is_present_again_is_refused(trial):
    trial.checkout.mkdir()
    refuse(trial, 'checkout remains')


def test_bundle_reached_through_a_link_or_a_relative_path_is_refused(trial):
    link = trial.tmp / 'link.bundle'
    link.symlink_to(trial.bundle)
    directory = trial.tmp / 'linked'
    directory.symlink_to(trial.tmp)
    for path in (link, directory / 'complete.bundle', 'complete.bundle', trial.tmp / 'linked' / '..' / 'complete.bundle'):
        refuse(trial, bundle=path)


def swap_when_opened(monkeypatch, kit, replace, after):
    """Run ``replace(path)`` once, just before or just after the descriptor of the bundle file is opened."""
    real, done = os.open, []

    def hostile(name, flags, *args, **kwargs):
        hit = name == kit.bundle.name and 'dir_fd' in kwargs and not done
        if hit and not after:
            done.append(replace(kit.bundle))
        descriptor = real(name, flags, *args, **kwargs)
        if hit and after:
            done.append(replace(kit.bundle))
        return descriptor

    monkeypatch.setattr(os, 'open', hostile)
    return done


def replace_with(kind, kit):
    def replace(path):
        original = path.rename(path.with_name('original.bundle'))
        if kind == 'symlink':
            path.symlink_to(bundle_of(kit, 'attacker.bundle', 'refs/heads/trial', 'refs/heads/feature'))
        elif kind == 'fifo':
            os.mkfifo(path)
        else:
            path.write_bytes(original.read_bytes())
        return kind
    return replace


@pytest.mark.parametrize('kind,after', [('symlink', False), ('symlink', True), ('fifo', False), ('fifo', True),
                                        ('copy', True)])
def test_bundle_path_swapped_around_the_open_is_refused(trial, monkeypatch, kind, after):
    done = swap_when_opened(monkeypatch, trial, replace_with(kind, trial), after)
    refuse(trial)
    assert done == [kind]


def test_directory_swapped_to_a_link_when_opened_is_refused(trial, monkeypatch):
    kept, decoy = trial.tmp / 'kept', trial.tmp / 'decoy'
    kept.mkdir()
    decoy.mkdir()
    trial.bundle.rename(kept / 'complete.bundle')
    (decoy / 'complete.bundle').write_bytes((kept / 'complete.bundle').read_bytes())
    real, done = os.open, []

    def hostile(name, flags, *args, **kwargs):
        if name == 'kept' and 'dir_fd' in kwargs and not done:
            done.append(kept.rename(trial.tmp / 'moved'))
            kept.symlink_to(decoy)
        return real(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', hostile)
    refuse(trial, bundle=kept / 'complete.bundle')
    assert done


def test_bundle_modified_in_place_during_verification_is_refused(trial, monkeypatch):
    real = runtime_git.git

    def hostile(checkout, *words, **kwargs):
        if words[:2] == ('bundle', 'verify'):
            with trial.bundle.open('ab') as stream:
                stream.write(b'x')
        return real(checkout, *words, **kwargs)

    monkeypatch.setattr(runtime_git, 'git', hostile)
    refuse(trial, 'changed')


def test_missing_or_moved_recovery_reference_is_refused(trial):
    git(trial.primary, 'update-ref', 'refs/heads/trial', trial.feature)
    refuse(trial, 'recovery reference changed')
    git(trial.primary, 'branch', '-D', 'trial')
    refuse(trial, 'could not be verified')


@pytest.mark.parametrize('branch,extra,match', [('trial', True, 'closed head'), ('renamed', False, 'closed head')])
def test_bundle_with_a_forged_or_missing_reference_is_refused(trial, branch, extra, match):
    refuse(trial, match, bundle=forged_branch_bundle(trial, branch, extra))


def test_bundle_with_a_tag_or_an_unpublished_extra_branch_is_refused(trial):
    git(trial.primary, 'tag', 'recovery', trial.head)
    refuse(trial, 'unsupported recovery reference',
           bundle=bundle_of(trial, 'tagged.bundle', 'refs/heads/trial', 'refs/tags/recovery'))
    git(trial.primary, 'checkout', '-q', '-b', 'wip', trial.feature)
    commit(trial.primary, 'unpublished')
    git(trial.primary, 'checkout', '-q', 'dev')
    refuse(trial, 'unpublished source', bundle=bundle_of(trial, 'wip.bundle', 'refs/heads/trial', 'refs/heads/wip'))


@pytest.mark.parametrize('name', [b'refs/heads/tri al', b'refs/heads/trial:refs/heads/dev',
                                  b'refs/heads/trial\nrefs/heads/dev'])
def test_hostile_reference_name_in_the_bundle_header_is_refused_and_fetches_nothing(trial, name):
    before = git(trial.primary, 'for-each-ref')
    trial.bundle.write_bytes(trial.bundle.read_bytes().replace(b'refs/heads/trial', name, 1))
    refuse(trial)
    assert git(trial.primary, 'for-each-ref') == before


def test_merge_carrying_a_change_no_automatic_merge_makes_is_refused(tmp_path):
    refuse(build(tmp_path, manual=True), 'meaningful merge resolution')


@pytest.mark.parametrize('forge', ['published', 'sha256', 'bundle', 'close', 'commits'])
def test_forged_archive_record_does_not_complete_the_scope(trial, forge):
    archive(trial)
    assert lifecycle.pending(trial.base, trial.owner) == []
    value = {'published': git(trial.primary, 'rev-parse', 'dev~2'), 'sha256': '0' * 64, 'commits': [],
             'bundle': str(bundle_of(trial, 'other.bundle', 'refs/heads/trial', 'refs/heads/feature')),
             'close': {'mode': 'bundle', 'at': 0}}[forge]
    lifecycle._edit(trial.base, lambda rows: next(iter(rows.values()))['archive'].update({forge: value}))
    assert lifecycle.pending(trial.base, trial.owner)


def test_recorded_disposition_cannot_be_replayed_for_another_worker(trial):
    archive(trial)
    lifecycle._edit(trial.base, lambda rows: rows.update(replay={**next(iter(rows.values())), 'owner': 'replayer'}))
    assert lifecycle.pending(trial.base, 'replayer')


def test_git_timeout_is_a_refusal_and_keeps_the_scope_pending(trial, monkeypatch):
    archive(trial)
    real = runtime_git.git

    def slow(checkout, *words, **kwargs):
        if 'fsck' in words:
            raise subprocess.TimeoutExpired('git', 60)
        return real(checkout, *words, **kwargs)

    monkeypatch.setattr(runtime_git, 'git', slow)
    assert lifecycle.pending(trial.base, trial.owner)
    with pytest.raises(Denied):
        archive(trial)


def test_claim_lost_during_verification_records_nothing(trial):
    answers = iter(['worker', ''])
    with pytest.raises(Denied, match='claim changed'):
        lifecycle.archive_obsolete(trial.base, 'worker', str(trial.checkout), str(trial.bundle),
                                   lambda _: next(answers))
    assert 'archive' not in lifecycle.scopes(trial.base, trial.owner)[0]


def stub_session(name, *claims):
    return SimpleNamespace(name=name, claims=lambda kind: [SimpleNamespace(owner=o, key=k) for o, k in claims])


def worktrees_command(kit, monkeypatch, session, *words):
    monkeypatch.setattr(task_cli.limits, 'root', lambda: kit.base)
    return task_cli._worktrees(cli.COMMANDS.parser().parse_args(['worktrees', *words]), session)


def test_cli_records_the_bundle_disposition_and_nothing_else(trial, monkeypatch):
    own = stub_session('worker', ('worker', str(trial.checkout)))
    result = worktrees_command(trial, monkeypatch, own, '--archive-obsolete', str(trial.checkout),
                               '--bundle', str(trial.bundle))
    assert result['disposition'] == 'archived-obsolete-trial'
    assert not {'landed', 'cleanup_verified', 'cleanup'} & set(result)
    scope = lifecycle.scopes(trial.base, 'worker')[0]
    assert 'cleanup' not in scope and scope['archive'] == result
    assert git(trial.primary, 'rev-parse', 'refs/heads/trial') == trial.head


def test_cli_needs_the_callers_own_live_claim(trial, monkeypatch):
    claimed = str(trial.checkout)
    for session in (stub_session('intruder', ('worker', claimed)), stub_session('worker'),
                    stub_session('worker', ('intruder', claimed))):
        with pytest.raises(Denied, match='live exact checkout claim'):
            worktrees_command(trial, monkeypatch, session, '--archive-obsolete', claimed, '--bundle', str(trial.bundle))
    assert 'archive' not in lifecycle.scopes(trial.base, trial.owner)[0]


@pytest.mark.parametrize('words', [('--archive-obsolete', 'CHECKOUT', '--cleanup', 'CHECKOUT', '--bundle', 'BUNDLE'),
                                   ('--archive-obsolete', 'CHECKOUT'), ('--bundle', 'BUNDLE')])
def test_cli_refuses_a_mixed_or_incomplete_request(trial, monkeypatch, words):
    own = stub_session('worker', ('worker', str(trial.checkout)))
    words = [{'CHECKOUT': str(trial.checkout), 'BUNDLE': str(trial.bundle)}.get(word, word) for word in words]
    with pytest.raises(ValueError):
        worktrees_command(trial, monkeypatch, own, *words)
    assert 'archive' not in lifecycle.scopes(trial.base, trial.owner)[0]
    assert lifecycle.pending(trial.base, trial.owner)


@pytest.mark.parametrize('words', [['--landed'], ['--force'], ['--mark-landed', 'x'], ['--published', 'x'],
                                   ['--sha256', 'x']])
def test_cli_has_no_flag_that_names_a_landing_a_publication_or_a_digest(words):
    with pytest.raises(SystemExit):
        cli.COMMANDS.parser().parse_args(['worktrees', *words])

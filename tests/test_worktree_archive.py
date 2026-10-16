"""Obsolete automatic trial merges never replace useful source ancestry."""

import time
from types import SimpleNamespace

import pytest
from test_trees import commit, git, make_repo, tree

from poolhouse import trees
from poolhouse.errors import Denied
from poolhouse.workspace import worktree_lifecycle as lifecycle


@pytest.fixture
def trial(tmp_path):
    primary = make_repo(tmp_path)
    checkout = tree(primary, 'trial', 'worker', time.time())
    git(primary, 'checkout', '-qb', 'feature')
    commit(primary, 'feature')
    feature = git(primary, 'rev-parse', 'HEAD')
    git(primary, 'checkout', 'dev')
    commit(primary, 'advance')
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
                           head=head, feature=feature, owner='worker')


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

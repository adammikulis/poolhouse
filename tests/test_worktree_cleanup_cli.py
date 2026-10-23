"""Owned checkout cleanup reserves and releases its exact scopes without user claim steps."""

import argparse

import pytest
from test_worktree_lifecycle import claim, setup as _setup

from poolhouse.board.client import Conflict
from poolhouse.errors import Denied
from poolhouse.workspace import integration_git as repo, task_cli, worktree_lifecycle as lifecycle

pytest_plugins = ['node_kit']
setup = _setup


def cleanup(kit, path=None):
    return task_cli._worktrees(argparse.Namespace(cleanup=str(path or kit.checkout), archive_obsolete='', bundle=''), kit.s)


def held(kit):
    return {(row.kind, row.key): row.lease for row in kit.s.claims() if row.owner == kit.name}


def test_cleanup_automatically_reserves_released_own_checkout_and_releases_removed_scopes(setup):
    kit = setup
    claim(kit)
    kit.s.release('worktree', str(kit.checkout.resolve()))
    unrelated = kit.s.claim('branch', 'unrelated-work')
    result = cleanup(kit)
    assert result['cleanup_verified'] and not kit.checkout.exists()
    assert held(kit) == {('branch', 'unrelated-work'): unrelated.lease}
    assert cleanup(kit) == result


def test_success_releases_existing_target_claims(setup):
    kit = setup
    claim(kit)
    kit.s.claim('branch', 'worker/change')
    cleanup(kit)
    assert held(kit) == {}


@pytest.mark.parametrize('kind', ['worktree', 'branch'])
def test_foreign_scope_conflict_does_not_take_partial_claims_or_remove_checkout(setup, kind):
    kit = setup
    claim(kit)
    kit.s.release('worktree', str(kit.checkout.resolve()))
    foreign = kit.node.session(kit.node.member('foreign-cleaner', parent=kit.lead))
    key = str(kit.checkout.resolve()) if kind == 'worktree' else 'worker/change'
    original = foreign.claim(kind, key)
    with pytest.raises(Conflict):
        cleanup(kit)
    assert kit.checkout.exists() and held(kit) == {}
    assert next(row for row in foreign.claims() if row.kind == kind and row.key == key).lease == original.lease


@pytest.mark.parametrize('preexisting', [False, True])
@pytest.mark.parametrize('problem', ['dirty', 'unmerged', 'locked'])
def test_refused_cleanup_preserves_preexisting_and_unrelated_claims_releasing_only_new(setup, preexisting, problem):
    kit = setup
    claim(kit)
    if not preexisting:
        kit.s.release('worktree', str(kit.checkout.resolve()))
    if problem == 'dirty':
        (kit.checkout / 'unique.txt').write_text('preserve')
    elif problem == 'unmerged':
        from harness_kit import commit
        commit(kit.checkout, 'unique.txt', 'unique', 'feat: unique work')
    else:
        repo.git(kit.primary, 'worktree', 'lock', str(kit.checkout))
    kit.s.claim('branch', 'unrelated-work')
    before = held(kit)
    with pytest.raises(Denied):
        cleanup(kit)
    assert kit.checkout.exists() and held(kit) == before


def test_primary_and_unattributed_checkout_cannot_be_reserved_for_cleanup(setup):
    kit = setup
    for path in (kit.primary, kit.checkout):
        with pytest.raises(Denied, match='attributed'):
            cleanup(kit, path)
    assert held(kit) == {} and kit.checkout.exists()


@pytest.mark.parametrize('change', ['foreign-lease', 'reacquired-lease', 'branch'])
def test_cleanup_rechecks_bound_identity_and_exact_leases_before_removal(setup, monkeypatch, change):
    kit = setup
    claim(kit)
    target = str(kit.checkout.resolve())
    if change == 'branch':
        repo.git(kit.checkout, 'switch', '-c', 'worker/second')
        lifecycle.remember(kit.base, kit.name, target)
        repo.git(kit.checkout, 'switch', 'worker/change')
    original = lifecycle.checkpoint
    replacement = []

    def changed(base, owner):
        original(base, owner)
        if change == 'branch':
            repo.git(kit.checkout, 'switch', 'worker/second')
        else:
            kit.s.release('worktree', target)
            cleaner = kit.node.session(kit.node.member('replacement-cleaner', parent=kit.lead)) if change == 'foreign-lease' else kit.s
            replacement.append(cleaner.claim('worktree', target))

    monkeypatch.setattr(lifecycle, 'checkpoint', changed)
    with pytest.raises(Denied, match='scope or live reservation changed'):
        cleanup(kit)
    assert kit.checkout.exists()
    if replacement:
        assert any(row.lease == replacement[0].lease for row in kit.s.claims())

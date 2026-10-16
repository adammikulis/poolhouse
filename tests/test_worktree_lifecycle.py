"""Completion checks retain checkout provenance after ephemeral ownership expires."""

import argparse
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from harness_kit import checkouts, commit
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse import claude, harness_claims, harnesshook, harnessid, harnessing
from poolhouse.errors import Denied
from poolhouse.workspace import (
    integration_git as repo,
    limits,
    notification_reader,
    worktree_lifecycle as lifecycle,
)


@pytest.fixture
def setup(workspace_node, tmp_path):  # noqa: F811
    kit = SimpleNamespace(node=workspace_node, base=limits.root(), **vars(checkouts(tmp_path)))
    kit.lead = workspace_node.member('lead')
    kit.sender = workspace_node.member('worker', parent=kit.lead)
    kit.name = kit.sender.name
    kit.s = workspace_node.session(kit.sender)
    return kit


def claim(kit, path=None, who=None):
    """The worker takes the claim on a checkout and the scope of it is recorded for the session."""
    path = str(Path(path or kit.checkout).resolve())
    session = who or kit.s
    session.claim('worktree', path)
    lifecycle.remember(kit.base, session.name, path)


def owner_of(session):
    return lambda path: next((c.owner for c in session.claims('worktree') if c.key == path), '')


def done(kit):
    """The check an announcement of done makes: no recorded checkout, registration or branch of the session remains."""
    lifecycle.require_clean(kit.base, kit.name)


def test_scoped_done_blocks_retained_checkout_after_claim_release(setup):
    kit = setup
    claim(kit)
    kit.s.release('worktree', str(kit.checkout.resolve()))
    for _ in range(3):
        with pytest.raises(Denied, match=r'checkout.*worker/change'):
            done(kit)
    assert kit.checkout.exists()
    with pytest.raises(Denied, match='verified cleanup'):
        lifecycle.require_clean(kit.base, kit.name)
    lifecycle.require_clean(kit.base, kit.name, within=(str(kit.base),))
    with pytest.raises(Denied, match='verified cleanup'):
        lifecycle.require_clean(kit.base, kit.name, within=(str(kit.checkout),))


def test_missing_checkout_does_not_hide_surviving_branch(setup):
    kit = setup
    claim(kit)
    repo.git(kit.primary, 'worktree', 'remove', str(kit.checkout))
    with pytest.raises(Denied, match='branches remain: worker/change'):
        done(kit)
    assert repo.git(kit.primary, 'rev-parse', 'worker/change')
    repo.git(kit.primary, 'branch', '-d', 'worker/change')
    with pytest.raises(Denied, match='cleanup proof is missing'):
        done(kit)


def test_foreign_checkout_and_primary_are_not_completion_scopes(setup):
    kit = setup
    foreign = kit.node.session(kit.node.member('foreign', parent=kit.lead))
    claim(kit, kit.checkout, foreign)
    claim(kit, kit.primary)
    done(kit)
    assert kit.checkout.exists()
    assert lifecycle.pending(kit.base, kit.name) == []
    with pytest.raises(Denied, match='verified cleanup'):
        lifecycle.require_clean(kit.base, foreign.name)


@pytest.mark.redteam
def test_completion_never_deletes_dirty_unique_or_ignored_work(setup):
    kit = setup
    claim(kit)
    (kit.checkout / 'source.py').write_text('value = 2\n')
    (kit.checkout / 'private.txt').write_text('preserved')
    (kit.checkout / '.gitignore').write_text('private.txt\n')
    before = {name: (kit.checkout / name).read_bytes() for name in ('source.py', 'private.txt', '.gitignore')}
    with pytest.raises(Denied, match='checkout remains'):
        done(kit)
    assert {name: (kit.checkout / name).read_bytes() for name in before} == before


def test_claim_expiry_does_not_erase_durable_checkout_attribution(setup):
    kit = setup
    path = str(kit.checkout.resolve())
    kit.s.claim('worktree', path, ttl_s=1)
    lifecycle.remember(kit.base, kit.name, path)
    end = time.monotonic() + 10
    while [c for c in kit.s.claims() if c.owner == kit.name] and time.monotonic() < end:
        time.sleep(0.1)
    assert [c for c in kit.s.claims() if c.owner == kit.name] == []
    with pytest.raises(Denied, match='verified cleanup'):
        done(kit)


def test_branch_changes_remain_tracked_until_every_attributed_branch_is_removed(setup):
    kit = setup
    claim(kit)
    repo.git(kit.checkout, 'switch', '-c', 'worker/second')
    claim(kit)
    repo.git(kit.primary, 'worktree', 'remove', str(kit.checkout))
    repo.git(kit.primary, 'branch', '-d', 'worker/second')
    with pytest.raises(Denied, match='worker/change'):
        done(kit)


@pytest.mark.redteam
def test_cached_done_rechecks_reappeared_branch(setup):
    kit = setup
    claim(kit)
    lifecycle.cleanup(kit.base, kit.name, str(kit.checkout), owner_of(kit.s))
    done(kit)
    repo.git(kit.primary, 'branch', 'worker/change')
    with pytest.raises(Denied, match='branches remain'):
        done(kit)


def test_native_mutation_records_its_unmanaged_checkout(setup):
    kit = setup
    harness_claims.reserve('Write', {'file_path': str(kit.checkout / 'source.py')},
                           str(kit.checkout), kit.name, [str(kit.checkout)])
    assert lifecycle.pending(kit.base, kit.name)[0]['branch'] == 'worker/change'
    with pytest.raises(Denied, match='verified cleanup'):
        done(kit)


@pytest.mark.redteam
def test_deleted_unique_branch_does_not_prove_recorded_work_landed(setup):
    kit = setup
    unique = commit(kit.checkout, 'source.py', 'value = 2\n', 'feat: unique work')
    claim(kit)
    repo.git(kit.primary, 'worktree', 'remove', str(kit.checkout))
    repo.git(kit.primary, 'branch', '-D', 'worker/change')
    with pytest.raises(Denied, match='recorded commits are not landed'):
        done(kit)
    repo.git(kit.primary, 'merge', '--ff-only', unique)
    with pytest.raises(Denied, match='cleanup proof is missing'):
        done(kit)


@pytest.mark.redteam
def test_harness_stop_uses_authenticated_owner_and_refuses_lingering_scope(setup):
    kit = setup
    claim(kit)
    rail = harnesshook.Rail('plan-and-go', kit.name, roots=[str(kit.checkout)])
    result = harnesshook.stop(rail)
    assert result['decision'] == 'block' and str(kit.checkout) in result['reason']
    other = harnesshook.Rail('plan-and-go', kit.node.member('other', parent=kit.lead).name, roots=[str(kit.checkout)])
    assert harnesshook.stop(other) == {}
    lifecycle.cleanup(kit.base, kit.name, str(kit.checkout), owner_of(kit.s))
    assert harnesshook.stop(rail) == {}


def test_claude_settings_wire_stop_and_subagent_stop():
    import json

    hooks = json.loads(claude.settings('PRE', 'POST', 300, 'STOP'))['hooks']
    assert hooks['Stop'][0]['hooks'][0]['command'] == 'STOP'
    assert hooks['SubagentStop'][0]['hooks'][0]['command'] == 'STOP'


def opened(kit, monkeypatch, messages):
    """The harness session of the worker over its recorded checkouts, as a launcher opens it."""
    monkeypatch.setattr(harnessid, 'announce', lambda *args: True)
    session = harnessid.HarnessSession(kit.name, base=kit.base)
    args = argparse.Namespace(project=str(kit.checkout), parent='', name=kit.name, role='plan-and-go',
                              orders_from='owner', session_factory=lambda *args: session)
    return harnessing.opened(args, 'codex', ('http://127.0.0.1:9', 'fixture', 0), messages.append)


@pytest.mark.redteam
def test_launcher_refuses_success_without_done_announcement(setup, monkeypatch):
    kit = setup
    claim(kit)
    messages = []
    with pytest.raises(ValueError, match='verified cleanup'), opened(kit, monkeypatch, messages):
        pass
    assert any('unfinished checkout:' in line for line in messages)
    assert kit.checkout.exists()


def test_precreation_claim_catches_checkout_without_another_mutation(setup):
    kit = setup
    target = kit.checkout.parent / 'reserved-checkout'
    claim(kit, target)
    assert lifecycle.pending(kit.base, kit.name) == []
    repo.git(kit.primary, 'worktree', 'add', '-b', 'worker/reserved', str(target))
    with pytest.raises(Denied, match=r'reserved-checkout.*worker/reserved'):
        done(kit)
    assert target.exists()


@pytest.mark.redteam
def test_materialized_reservation_retains_branch_and_commit_after_removal(setup):
    kit = setup
    target = kit.checkout.parent / 'reserved-checkout'
    claim(kit, target)
    repo.git(kit.primary, 'worktree', 'add', '-b', 'worker/reserved', str(target))
    unique = commit(target, 'source.py', 'value = 2\n', 'feat: reserved work')
    assert lifecycle.pending(kit.base, kit.name)[0]['branch'] == 'worker/reserved'
    repo.git(kit.primary, 'worktree', 'remove', str(target))
    with pytest.raises(Denied, match='branches remain: worker/reserved'):
        done(kit)
    repo.git(kit.primary, 'branch', '-D', 'worker/reserved')
    with pytest.raises(Denied, match='recorded commits are not landed'):
        done(kit)
    repo.git(kit.primary, 'merge', '--ff-only', unique)
    with pytest.raises(Denied, match='cleanup proof is missing'):
        done(kit)


@pytest.mark.redteam
def test_reserved_path_materialized_as_primary_repository_remains_pending(setup):
    kit = setup
    target = kit.checkout.parent / 'reserved-primary'
    claim(kit, target)
    target.mkdir()
    repo.git(target, 'init', '-b', 'development')
    assert lifecycle.pending(kit.base, kit.name)[0]['reasons'] == ['reserved checkout path remains']


@pytest.mark.redteam
def test_native_mutation_records_target_checkout_instead_of_working_directory(setup):
    kit = setup
    target = kit.checkout.parent / 'other-checkout'
    repo.git(kit.primary, 'worktree', 'add', '-b', 'worker/other', str(target))
    harness_claims.reserve('Write', {'file_path': str(target / 'source.py')},
                           str(kit.checkout), kit.name, [str(kit.checkout), str(target)])
    scopes = lifecycle.pending(kit.base, kit.name)
    assert [scope['path'] for scope in scopes] == [str(target)]


@pytest.mark.redteam
def test_launcher_exception_reports_pending_scope_and_preserves_failure(setup, monkeypatch):
    kit = setup
    claim(kit)
    messages = []
    with pytest.raises(OSError, match='fixture process failed'), opened(kit, monkeypatch, messages):
        messages.clear()
        raise OSError('fixture process failed')
    assert any('unfinished checkout:' in line for line in messages)
    assert kit.checkout.exists()


@pytest.mark.redteam
def test_nested_reservation_retains_checkout_provenance(setup):
    kit = setup
    target = kit.checkout.parent / 'reserved-checkout'
    claim(kit, target / 'nested')
    repo.git(kit.primary, 'worktree', 'add', '-b', 'worker/reserved', str(target))
    assert lifecycle.pending(kit.base, kit.name)[0]['path'] == str(target)
    repo.git(kit.primary, 'worktree', 'remove', str(target))
    with pytest.raises(Denied, match='branches remain: worker/reserved'):
        done(kit)


@pytest.mark.redteam
@pytest.mark.parametrize('failure', [RuntimeError('fixture inspection failed'), Denied('fixture inspection refused')])
def test_launcher_exception_survives_failed_exit_inspection(setup, monkeypatch, failure):
    kit = setup
    messages = []
    with pytest.raises(OSError, match='fixture process failed'), opened(kit, monkeypatch, messages):
        monkeypatch.setattr(harnessid.HarnessSession, 'pending_worktrees', lambda self: (_ for _ in ()).throw(failure))
        raise OSError('fixture process failed')
    assert any('checkout inspection failed:' in line for line in messages)


@pytest.mark.redteam
def test_claim_before_final_commit_cannot_complete_after_lost_checkout_and_branch(setup):
    kit = setup
    claim(kit)
    commit(kit.checkout, 'source.py', 'value = 2\n', 'feat: uncaptured final commit')
    repo.git(kit.primary, 'worktree', 'remove', str(kit.checkout))
    repo.git(kit.primary, 'branch', '-D', 'worker/change')
    lifecycle.remember(kit.base, kit.name, str(kit.checkout))
    with pytest.raises(Denied, match='cleanup proof is missing'):
        done(kit)


@pytest.mark.redteam
def test_pretool_invalidates_old_cleanup_proof_when_tool_removes_its_final_commit(setup, monkeypatch):
    kit = setup
    claim(kit)
    lifecycle.cleanup(kit.base, kit.name, str(kit.checkout), owner_of(kit.s))
    repo.git(kit.primary, 'worktree', 'add', '-b', 'worker/change', str(kit.checkout))
    harness_claims.reserve('Write', {'file_path': str(kit.checkout / 'source.py')},
                           str(kit.checkout), kit.name, [str(kit.checkout)])
    commit(kit.checkout, 'source.py', 'value = 2\n', 'feat: vanished tool commit')
    repo.git(kit.primary, 'worktree', 'remove', str(kit.checkout))
    repo.git(kit.primary, 'branch', '-D', 'worker/change')
    monkeypatch.setattr(harnesshook, 'nudge', lambda *_, **__: '')
    assert harnesshook.post(kit.name, harnesshook.Rail('plan-and-go', kit.name, [str(kit.checkout)])) == {}
    with pytest.raises(Denied, match='cleanup proof is missing'):
        done(kit)


def test_posttool_captures_final_commit_and_cleanup_proves_it_landed(setup, monkeypatch):
    kit = setup
    claim(kit)
    commit_id = commit(kit.checkout, 'source.py', 'value = 2\n', 'feat: captured final commit')
    monkeypatch.setattr(harnesshook, 'nudge', lambda *_, **__: notification_reader.checkpoint(kit.base, kit.name) or '')
    harnesshook.post(kit.name, harnesshook.Rail('plan-and-go', kit.name, [str(kit.checkout)]))
    assert commit_id in lifecycle.scopes(kit.base, kit.name)[0]['commits']
    with pytest.raises(Denied, match='commits outside'):
        lifecycle.cleanup(kit.base, kit.name, str(kit.checkout), owner_of(kit.s))
    assert kit.checkout.exists()
    repo.git(kit.primary, 'merge', '--ff-only', commit_id)
    result = lifecycle.cleanup(kit.base, kit.name, str(kit.checkout), owner_of(kit.s))
    assert result['cleanup_verified'] and result['commit'] == commit_id
    done(kit)


@pytest.mark.redteam
def test_cleanup_never_takes_a_foreign_live_claim(setup):
    kit = setup
    claim(kit)
    kit.s.release('worktree', str(kit.checkout.resolve()))
    foreign = kit.node.session(kit.node.member('foreign', parent=kit.lead))
    foreign.claim('worktree', str(kit.checkout.resolve()))
    with pytest.raises(Denied, match='live checkout claim'):
        lifecycle.cleanup(kit.base, kit.name, str(kit.checkout), owner_of(kit.s))
    assert kit.checkout.exists()
    assert owner_of(kit.s)(str(kit.checkout.resolve())) == foreign.name


def test_authenticated_worktree_cleanup_cli_records_the_exact_landed_commit(setup):
    from poolhouse.workspace import task_cli

    kit = setup
    claim(kit)
    result = task_cli._worktrees(argparse.Namespace(cleanup=str(kit.checkout), archive_obsolete='', bundle=''), kit.s)
    assert result['cleanup_verified']
    assert lifecycle.pending(kit.base, kit.name) == []

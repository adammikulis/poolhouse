"""Canonical command routing and exact bridge claim transactions."""

from __future__ import annotations

import pytest
from git_world import Project, git

from poolhouse import (
    home,
    runtime,
    runtime_bridges,
    runtime_deploy,
    runtime_launchers,
)

SCRIPTS = {"poolhouse-workspace": "poolhouse.workspace.cli:main"}


@pytest.fixture
def routing(tmp_path, monkeypatch):
    state = tmp_path / "account" / ".poolhouse"
    monkeypatch.setenv("POOLHOUSE_HOME", str(state))
    monkeypatch.setattr(home, "account_roots", lambda: (state, tmp_path / "cache"))
    shared = state.parent / ".local" / "bin"
    shared.mkdir(parents=True)
    base = tmp_path / "git"
    base.mkdir()
    project = Project(base)
    Project.write(project.root, "pyproject.toml", '[project.scripts]\npoolhouse-workspace = "poolhouse.workspace.cli:main"\n')
    Project.commit(project.root, "chore: command inventory")
    sha = git(project.root, "rev-parse", "HEAD")
    git(project.root, "update-ref", "refs/remotes/origin/devline", sha)
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "devline")
    plan = runtime_deploy.Plan(project.root, sha, runtime_launchers.directory())
    return plan, shared


def test_only_missing_or_changed_exact_bridges_need_claims(routing):
    plan, shared = routing
    expected = runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers)
    [target] = expected
    assert target == shared / "poolhouse-workspace"
    assert target in runtime_deploy._switch_paths(plan)
    previous = runtime_bridges.snapshot(expected, frozenset({target}))
    runtime_bridges.install(expected, previous)
    info = target.stat()
    assert target not in runtime_deploy._switch_paths(plan)
    assert runtime_bridges.snapshot(expected, frozenset()) == {}
    runtime_bridges.install(expected, {})
    assert (target.stat().st_ino, target.stat().st_mtime_ns) == (info.st_ino, info.st_mtime_ns)


def test_bridge_restore_touches_only_exact_claimed_files(routing):
    plan, shared = routing
    expected = runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers)
    [target] = expected
    target.write_bytes(b"previous command")
    target.chmod(0o700)
    previous = runtime_bridges.snapshot(expected, frozenset({target}))
    runtime_bridges.install(expected, previous)
    unrelated = shared / "poolhouse-another-command"
    unrelated.write_text("concurrent work")
    runtime_bridges.restore(previous, expected)
    assert target.read_bytes() == b"previous command"
    assert unrelated.read_text() == "concurrent work"


def test_unclaimed_or_replaced_bridge_is_refused_before_write(routing):
    plan, _ = routing
    expected = runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers)
    [target] = expected
    with pytest.raises(OSError, match="changed while"):
        runtime_bridges.snapshot(expected, frozenset())
    previous = runtime_bridges.snapshot(expected, frozenset({target}))
    target.write_text("intervening command")
    target.chmod(0o700)
    with pytest.raises(OSError, match="changed during"):
        runtime_bridges.install(expected, previous)
    assert target.read_text() == "intervening command"


def test_symlink_bridge_and_redirected_shared_parent_are_refused(routing, tmp_path):
    plan, shared = routing
    expected = runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers)
    [target] = expected
    victim = tmp_path / "victim"
    victim.write_text("private")
    target.symlink_to(victim)
    with pytest.raises(OSError, match="symbolic"):
        runtime_bridges.changes(expected)
    target.unlink()
    shared.rmdir()
    shared.symlink_to(tmp_path)
    with pytest.raises(OSError, match="symbolic"):
        runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers)
    assert victim.read_text() == "private"


def test_failed_publication_restores_canonical_and_exact_bridges(routing, tmp_path, monkeypatch):
    plan, shared = routing
    expected = runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers)
    [bridge] = expected
    unrelated = shared / "poolhouse-unrelated"
    unrelated.write_text("preserve")
    runtime_deploy.prepare_root()
    selected = runtime.directory() / "selected.json"
    selected.write_text("previous selection")
    chosen = runtime.Runtime(tmp_path / "candidate", plan.commit, "0", runtime.identity())
    monkeypatch.setattr(runtime, "verify", lambda candidate: candidate)
    monkeypatch.setattr(runtime_launchers, "entrypoints", lambda _chosen: SCRIPTS)

    def prepare(directory, _chosen, **_options):
        (directory / "poolhouse-workspace").write_text("prepared")
        return [directory / "poolhouse-workspace"]

    def refuse(_chosen):
        assert bridge.read_bytes() == expected[bridge]
        raise ValueError("selection refused")

    monkeypatch.setattr(runtime_launchers, "install", prepare)
    monkeypatch.setattr(runtime, "publish", refuse)
    with pytest.raises(ValueError, match="selection refused"):
        runtime_deploy.switch(chosen, plan, claimed=frozenset(runtime_deploy._switch_paths(plan)))
    assert selected.read_text() == "previous selection"
    assert not bridge.exists()
    assert not (plan.launchers / "poolhouse-workspace").exists()
    assert unrelated.read_text() == "preserve"


def test_declared_metadata_mismatch_leaves_every_entrypoint_untouched(routing, tmp_path, monkeypatch):
    plan, shared = routing
    chosen = runtime.Runtime(tmp_path / "candidate", plan.commit, "0", runtime.identity())
    monkeypatch.setattr(runtime, "verify", lambda candidate: candidate)
    monkeypatch.setattr(runtime_launchers, "entrypoints", lambda _chosen: {"poolhouse": "poolhouse.cli:main"})
    with pytest.raises(runtime_deploy.DeployError, match="metadata"):
        runtime_deploy.switch(chosen, plan, claimed=frozenset(runtime_deploy._switch_paths(plan)))
    assert not list(shared.iterdir())
    assert not plan.launchers.exists()


def test_shared_bridge_directory_ignores_ambient_home(routing, tmp_path, monkeypatch):
    plan, shared = routing
    monkeypatch.setenv("HOME", str(tmp_path / "hostile-home"))
    expected = runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers)
    assert {path.parent for path in expected} == {shared}


def test_nonexecutable_bridge_is_repaired_under_its_exact_claim(routing):
    plan, _ = routing
    expected = runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers)
    [target] = expected
    target.write_bytes(expected[target])
    target.chmod(0o600)
    assert runtime_bridges.changes(expected) == [target]
    previous = runtime_bridges.snapshot(expected, frozenset({target}))
    runtime_bridges.install(expected, previous)
    assert target.stat().st_mode & 0o100
    runtime_bridges.restore(previous, expected)
    assert target.stat().st_mode & 0o777 == 0o600


def test_foreign_reviewed_console_metadata_is_refused_before_commands_change(routing):
    plan, shared = routing
    Project.write(plan.checkout, "pyproject.toml", '[project.scripts]\nforeign-command = "os:system"\n')
    Project.commit(plan.checkout, "chore: hostile inventory")
    sha = git(plan.checkout, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="belong"):
        runtime_bridges.planned(plan.checkout, sha, plan.launchers)
    assert not list(shared.iterdir())


def test_moved_runtime_state_never_writes_account_command_bridges(routing, tmp_path, monkeypatch):
    plan, _ = routing
    actual = tmp_path / "another-account" / ".poolhouse"
    monkeypatch.setattr(home, "account_roots", lambda: (actual, tmp_path / "cache"))
    assert runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers) == {}
    assert not actual.parent.exists()


def test_missing_session_never_fabricates_shared_install_claims(routing, monkeypatch):
    plan, _ = routing
    monkeypatch.setattr(runtime_deploy.person, "marked", lambda: False)
    with runtime_deploy.owning(runtime_deploy._switch_paths(plan), wait_s=0, who=None) as claimed:
        assert claimed == frozenset()
        expected = runtime_bridges.planned(plan.checkout, plan.commit, plan.launchers)
        with pytest.raises(OSError, match="changed while"):
            runtime_bridges.snapshot(expected, claimed)
    monkeypatch.setattr(runtime_deploy.person, "marked", lambda: True)
    with pytest.raises(runtime_deploy.DeployError, match="authenticated workspace agent"), \
            runtime_deploy.owning(runtime_deploy._switch_paths(plan), wait_s=0, who=None):
        pytest.fail("an agent without a session obtained install claims")


def test_recovery_claims_come_from_the_actual_target_commit(routing):
    plan, shared = routing
    Project.write(plan.checkout, "pyproject.toml", '[project.scripts]\npoolhouse = "poolhouse.cli:main"\n')
    Project.commit(plan.checkout, "chore: other command inventory")
    target = git(plan.checkout, "rev-parse", "HEAD")
    paths = runtime_deploy._switch_paths(plan, target)
    assert shared / "poolhouse" in paths
    assert shared / "poolhouse-workspace" not in paths


def test_unverified_prefix_never_executes_command_metadata(routing, tmp_path, monkeypatch):
    plan, shared = routing
    chosen = runtime.Runtime(tmp_path / "unverified", plan.commit, "0", runtime.identity())

    def unexpected(_chosen):
        pytest.fail("unverified runtime reached metadata execution")

    monkeypatch.setattr(runtime_launchers, "entrypoints", unexpected)
    with pytest.raises((OSError, ValueError)):
        runtime_deploy.switch(chosen, plan, claimed=frozenset(runtime_deploy._switch_paths(plan)))
    assert not list(shared.iterdir())

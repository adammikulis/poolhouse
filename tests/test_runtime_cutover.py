"""Runtime selection and supervisor replacement share the actual landing runner's atomic boundary."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_runtime_trust import commit, git

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import land_recover

from poolhouse import node_supervise, runtime, runtime_deploy, runtime_store, worktreerules
from poolhouse.lock import Busy, only_one


@pytest.fixture
def source(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "dev")
    head = commit(repo, "file")
    git(repo, "update-ref", "refs/remotes/origin/dev", head)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("POOLHOUSE_DEV_BRANCH", "dev")
    runtime_store.write_state({"checkout": str(repo)})
    return repo, head


def test_runtime_and_real_runner_use_the_same_lock_even_from_a_worktree(source, tmp_path, monkeypatch):
    repo, head = source
    checkout = tmp_path / "integration"
    git(repo, "worktree", "add", "--detach", str(checkout), head)
    runner = land_recover.RunnerLock(repo)
    assert runner.acquire()
    try:
        monkeypatch.setenv("GIT_DIR", checkout.joinpath(".git").read_text().split(": ", 1)[1].strip())
        monkeypatch.setenv("GIT_WORK_TREE", str(checkout))
        assert worktreerules.landing_lock(checkout) == runner.path
        with pytest.raises(Busy), only_one(worktreerules.landing_lock(checkout), wait=False):
            pytest.fail("runtime entered a live landing boundary")
    finally:
        runner.release()


def test_supplied_environment_is_sanitized_without_losing_authentication():
    supplied = {"GIT_DIR": "/other", "GIT_WORK_TREE": "/tree", "GIT_INDEX_FILE": "/index",
                "GIT_SSH_COMMAND": "trusted-ssh", "SSH_AUTH_SOCK": "/socket", "GIT_CONFIG_GLOBAL": "/config"}
    assert worktreerules._git_environment(supplied) == {key: value for key, value in supplied.items()
                                                       if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"}}
    assert supplied["GIT_DIR"] == "/other"


def test_switch_refuses_unpublished_source_and_rechecks_refs_inside_the_boundary(source, tmp_path, monkeypatch):
    repo, head = source
    chosen = runtime.Runtime(tmp_path / "runtime", head, "0", runtime.identity())
    called = []
    monkeypatch.setattr(runtime, "publish", lambda item: called.append(item))
    plan = runtime_deploy.Plan(repo, head, None)
    git(repo, "update-ref", "-d", "refs/remotes/origin/dev")
    with pytest.raises(runtime_deploy.DeployError):
        runtime_deploy.switch(chosen, plan)
    assert called == []
    git(repo, "update-ref", "refs/remotes/origin/dev", head)
    runner = land_recover.RunnerLock(repo)
    assert runner.acquire()
    try:
        with pytest.raises(Busy):
            runtime_deploy.switch(chosen, plan)
        assert called == []
    finally:
        runner.release()
    runtime_deploy.switch(chosen, plan)
    assert called == [chosen]


def test_current_source_floor_is_rechecked_before_selection(source, tmp_path, monkeypatch):
    repo, head = source
    plan = runtime_deploy.Plan(repo, head, None)
    chosen = runtime.Runtime(tmp_path / "runtime", head, "0", runtime.identity())
    latest = commit(repo, "packaging/runtime-floor", "2")
    git(repo, "update-ref", "refs/remotes/origin/dev", latest)
    called = []
    monkeypatch.setattr(runtime, "publish", lambda item: called.append(item))
    with pytest.raises(runtime_deploy.DeployError, match="source floor"):
        runtime_deploy.switch(chosen, plan)
    assert called == []


class WaitingChild:
    returncode = None

    def wait(self, timeout):
        raise subprocess.TimeoutExpired("scratch-child", timeout)


def test_supervisor_defers_automatic_stop_and_stops_under_the_runner_boundary(source, tmp_path, monkeypatch):
    repo, _ = source
    state = tmp_path / "node"
    state.mkdir()
    seen = node_supervise.signature(state)
    node_supervise.point(state, tmp_path / "replacement", "checksum")
    runner = land_recover.RunnerLock(repo)
    assert runner.acquire()
    child = WaitingChild()
    calls = []
    turns = 0

    def stopping():
        nonlocal turns
        turns += 1
        assert calls == []
        if turns == 2:
            runner.release()
        return False

    def end(item, folder):
        assert item is child and folder == state
        assert not runner.acquire(), "supervisor must hold the same lock throughout termination"
        calls.append("stopped")
        child.returncode = 0

    monkeypatch.setattr(node_supervise, "end", end)
    try:
        assert node_supervise._wait(child, state, seen, stopping) is None
    finally:
        runner.release()
    assert calls == ["stopped"] and turns == 2


def test_explicit_supervisor_shutdown_does_not_wait_for_landing(source, tmp_path, monkeypatch):
    repo, _ = source
    runner = land_recover.RunnerLock(repo)
    assert runner.acquire()
    try:
        assert node_supervise._wait(WaitingChild(), tmp_path, (b"", b""), lambda: True) is None
    finally:
        runner.release()


def test_person_unmerged_exception_does_not_authorize_a_different_recovery_commit(source, tmp_path, monkeypatch):
    repo, head = source
    unrelated = commit(repo, "unpublished")
    chosen = runtime.Runtime(tmp_path / "runtime", unrelated, "0", runtime.identity())
    monkeypatch.setattr(runtime_deploy.person, "marked", lambda: False)
    monkeypatch.setattr(runtime_deploy.person, "is_terminal", lambda _stream: True)
    with pytest.raises(runtime_deploy.DeployError, match="published at origin"):
        runtime_deploy.switch(chosen, runtime_deploy.Plan(repo, head, None, allow_unmerged=True))


def test_busy_landing_keeps_the_prepared_runtime_and_current_selection(source, monkeypatch):
    repo, head = source
    runtime_deploy.prepare_root()
    selected = runtime.directory() / "selected.json"
    selected.write_bytes(b"existing-selection")
    runner = land_recover.RunnerLock(repo)
    assert runner.acquire()
    prepared = []

    def builder(plan, stage, prefix):
        prefix.mkdir(parents=True)
        chosen = runtime.Runtime(prefix, plan.commit, "0", runtime.identity())
        prepared.append(chosen)
        return chosen

    monkeypatch.setattr(runtime_deploy, "acting", lambda _plan: None)
    monkeypatch.setattr(runtime_deploy, "smoke", lambda *_args: None)
    try:
        with pytest.raises(Busy):
            runtime_deploy.build_and_switch(runtime_deploy.Plan(repo, head, None), builder)
    finally:
        runner.release()
    assert len(prepared) == 1 and prepared[0].prefix.is_dir()
    assert runtime_store.verified_at(prepared[0].prefix) > 0
    assert selected.read_bytes() == b"existing-selection"


def test_missing_source_context_defers_automatic_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "home"))
    state = tmp_path / "node"
    state.mkdir()
    seen = node_supervise.signature(state)
    node_supervise.point(state, tmp_path / "replacement", "checksum")
    turns, ended = [], []
    monkeypatch.setattr(node_supervise, "end", lambda *args: ended.append(args))

    def stopping():
        turns.append(True)
        return len(turns) == 2

    assert node_supervise._wait(WaitingChild(), state, seen, stopping) is None
    assert turns == [True, True] and ended == []

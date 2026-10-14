"""Runtime selection and supervisor replacement share the actual landing runner's atomic boundary."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from test_runtime_trust import commit, git

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import land_recover

from poolhouse import (
    node_binary,
    node_launch,
    node_supervise,
    runtime,
    runtime_deploy,
    runtime_source,
    runtime_store,
    worktreerules,
)
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


def test_supervisor_holds_boundary_until_replacement_answers(source, tmp_path, monkeypatch):
    repo, _ = source
    state = tmp_path / "node"
    state.mkdir()
    node_supervise.point(state, tmp_path / "replacement", "new")
    runner = land_recover.RunnerLock(repo)
    child, replacement = WaitingChild(), WaitingChild()
    calls = []
    monkeypatch.setattr(node_supervise, "resolve", lambda _: (tmp_path / "new", "new"))
    monkeypatch.setattr(node_supervise, "_program", lambda *args: tmp_path)
    monkeypatch.setattr(node_supervise, "_admitted", lambda *args: None)
    monkeypatch.setattr(node_supervise, "running", lambda _: {"binary": str(tmp_path / "old"), "sha256": "old"})

    def end(item, folder):
        assert item is child and folder == state
        assert not runner.acquire()
        calls.append("stopped")

    def spawn(*args):
        assert not runner.acquire()
        calls.append("spawned")
        return replacement

    def ready(*args):
        assert not runner.acquire(), "runner must stay excluded until verified hello"
        calls.append("healthy")
        return True

    monkeypatch.setattr(node_supervise, "end", end)
    monkeypatch.setattr(node_supervise, "_spawn", spawn)
    monkeypatch.setattr(node_supervise, "_ready", ready)
    custody = (None, node_supervise._identity(state))
    assert runner.acquire()
    try:
        assert node_supervise._cutover(child, state, [], custody, lambda: False) is None
        assert calls == []
    finally:
        runner.release()
    result = node_supervise._cutover(child, state, [], custody, lambda: False)
    assert result == (replacement, node_supervise.signature(state))
    assert calls == ["stopped", "spawned", "healthy"]
    assert runner.acquire()
    runner.release()


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
    monkeypatch.setattr(runtime_source.person, "marked", lambda: False)
    monkeypatch.setattr(runtime_source.person, "is_terminal", lambda _stream: True)
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


def test_changed_device_identity_keeps_current_node(source, tmp_path, monkeypatch):
    state = tmp_path / "node"
    state.mkdir()
    custody = (None, node_supervise._identity(state))
    (state / "device.cert").write_text("changed")
    stopped = []
    monkeypatch.setattr(node_supervise, "end", lambda *args: stopped.append(args))
    assert node_supervise._cutover(WaitingChild(), state, [], custody, lambda: False) is None
    assert stopped == []


def test_direct_swap_keeps_runner_excluded_until_hello(source, tmp_path, monkeypatch):
    repo, _ = source
    prefix, state = tmp_path / "runtime", tmp_path / "node"
    runner = land_recover.RunnerLock(repo)
    events = []
    monkeypatch.setattr(runtime, "selection_prefix", lambda: prefix)
    monkeypatch.setattr(node_launch.node_binary, "record_of", lambda _: {"sha256": "new"})
    monkeypatch.setattr(node_launch.node_binary, "verified", lambda _: prefix / "node")
    monkeypatch.setattr(node_supervise, "_admitted", lambda *args: None)
    monkeypatch.setattr(node_launch, "node_health", lambda _: {"pid": 1})
    monkeypatch.setattr(node_supervise, "running", lambda _: {"pid": 1, "sha256": "old"})
    monkeypatch.setattr(node_supervise, "pointer", lambda _: {})
    monkeypatch.setattr(node_launch, "supervised", lambda _: False)

    def step(name):
        assert not runner.acquire()
        events.append(name)

    monkeypatch.setattr(node_supervise, "point", lambda *args: step("point"))
    monkeypatch.setattr(node_launch, "stop_node", lambda *args: step("stop"))
    monkeypatch.setattr(node_launch, "_start_supervisor", lambda *args: step("start"))

    def answer(*args):
        step("hello")
        return {"pid": 2}

    monkeypatch.setattr(node_launch, "_answers_as", answer)
    assert runner.acquire()
    try:
        assert node_launch.swap(state)["action"] == "deferred"
        assert events == []
    finally:
        runner.release()
    assert node_launch.swap(state)["action"] == "swapped"
    assert events == ["point", "stop", "start", "hello"]
    assert runner.acquire()
    runner.release()


@pytest.mark.parametrize("refusal", ["unpublished", "rejected", "floor"])
def test_prior_node_recovery_requires_publication_and_current_floor(source, tmp_path, refusal):
    repo, head = source
    binary = tmp_path / "binary"
    binary.write_text("node")
    prefix = tmp_path / "prior"
    if refusal == "unpublished":
        head = commit(repo, "unpublished")
    record = node_binary.install(prefix, binary, commit=head)
    if refusal == "rejected":
        (prefix / runtime_store.REJECTED).write_text("rejected")
    if refusal == "floor":
        (repo / "packaging").mkdir()
        (repo / "packaging/runtime-floor").write_text("1")
        git(repo, "add", "packaging/runtime-floor")
        raised = commit(repo, "raised")
        git(repo, "update-ref", "refs/remotes/origin/dev", raised)
    with pytest.raises((node_binary.NodeBinaryError, runtime_source.DeployError)):
        node_supervise._admitted(node_binary.location(prefix), record["sha256"], repo)


def test_supervised_observer_defers_timeout_without_rollback_when_landing_starts(source, tmp_path, monkeypatch):
    repo, _ = source
    prefix, state = tmp_path / "runtime", tmp_path / "node"
    runner = land_recover.RunnerLock(repo)
    points = []
    monkeypatch.setattr(runtime, "selection_prefix", lambda: prefix)
    monkeypatch.setattr(node_binary, "record_of", lambda _: {"sha256": "new"})
    monkeypatch.setattr(node_binary, "verified", lambda _: prefix / "node")
    monkeypatch.setattr(node_supervise, "_admitted", lambda *args: None)
    monkeypatch.setattr(node_launch, "node_health", lambda _: {"pid": 1})
    monkeypatch.setattr(node_supervise, "running", lambda _: {"pid": 1, "sha256": "old"})
    monkeypatch.setattr(node_supervise, "pointer", lambda _: {})
    monkeypatch.setattr(node_launch, "supervised", lambda _: True)
    monkeypatch.setattr(node_supervise, "point", lambda *args: points.append(args))

    def timeout(*args):
        assert runner.acquire()
        return None

    monkeypatch.setattr(node_launch, "_until", timeout)
    try:
        assert node_launch.swap(state, wait_s=.01)["action"] == "deferred"
        assert points == [(state, None)]
    finally:
        runner.release()


def test_initial_certificate_binds_after_hello_once(source, tmp_path, monkeypatch):
    state = tmp_path / "node"
    state.mkdir()
    child = WaitingChild()
    child.pid = 42
    stopping = []
    bindings = []
    monkeypatch.setattr(node_supervise, "resolve", lambda _: (tmp_path / "binary", "sha"))
    monkeypatch.setattr(node_supervise, "_program", lambda *args: tmp_path)
    monkeypatch.setattr(node_supervise.node_key, "release", lambda _: None)
    monkeypatch.setattr(node_supervise, "_spawn", lambda *args: child)

    def ready(*args):
        (state / "device.cert").write_text("initial")
        return True

    def wait(*args):
        if not (state / "device.cert").exists():
            (state / "device.cert").write_text("initial")
        return None

    def cutover(item, folder, extra, custody, stop):
        bindings.append(custody[1])
        assert custody[1] == node_supervise._identity(state)
        stopping.append(True)
        child.returncode = 0
        return child, node_supervise.signature(state)

    monkeypatch.setattr(node_supervise, "_ready", ready)
    monkeypatch.setattr(node_supervise, "_wait", wait)
    monkeypatch.setattr(node_supervise, "_cutover", cutover)
    assert node_supervise._loop(state, [], lambda: bool(stopping)) == 0
    assert len(bindings) == 1


@pytest.mark.parametrize("failure", ["spawn", "readiness"])
def test_replacement_failure_restores_prior_while_runner_stays_excluded(source, tmp_path, monkeypatch, failure):
    repo, _ = source
    state = tmp_path / "node"
    state.mkdir()
    child, replacement, restored = WaitingChild(), WaitingChild(), WaitingChild()
    runner = land_recover.RunnerLock(repo)
    events = []
    monkeypatch.setattr(node_supervise, "resolve", lambda _: (tmp_path / "new", "new"))
    monkeypatch.setattr(node_supervise, "_program", lambda *args: tmp_path)
    monkeypatch.setattr(node_supervise, "_admitted", lambda *args: events.append("admit " + args[1]))
    monkeypatch.setattr(node_supervise, "running", lambda _: {"binary": str(tmp_path / "old"), "sha256": "old"})
    monkeypatch.setattr(node_supervise, "end", lambda *args: events.append("end"))
    monkeypatch.setattr(node_supervise, "point", lambda *args: events.append("pin " + args[2]))

    def spawn(folder, binary, sha, extra, key):
        assert not runner.acquire()
        events.append("spawn " + sha)
        if sha == "new" and failure == "spawn":
            raise OSError("spawn refused")
        return replacement if sha == "new" else restored

    def ready(item, folder, sha, stopping):
        assert not runner.acquire()
        events.append("hello " + sha)
        if sha == "new":
            raise OSError("hello refused")
        return True

    monkeypatch.setattr(node_supervise, "_spawn", spawn)
    monkeypatch.setattr(node_supervise, "_ready", ready)
    result = node_supervise._cutover(child, state, [], (None, node_supervise._identity(state)), lambda: False)
    assert result == (restored, node_supervise.signature(state))
    assert "admit old" in events and "pin old" in events and events[-1] == "hello old"
    assert runner.acquire()
    runner.release()


def test_initial_identity_binding_failure_stops_owned_child(tmp_path, monkeypatch):
    child = WaitingChild()
    child.poll = lambda: None
    stopped = []
    monkeypatch.setattr(node_supervise, "_ready", lambda *args: True)
    monkeypatch.setattr(node_supervise, "_identity", lambda *args: (_ for _ in ()).throw(OSError("binding refused")))
    monkeypatch.setattr(node_supervise, "end", lambda *args: stopped.append(args))
    assert node_supervise._initial_identity(child, tmp_path, "sha", lambda: False) is None
    assert stopped == [(child, tmp_path)]


def test_spawn_metadata_failure_stops_new_child_before_prior_recovery(source, tmp_path, monkeypatch):
    _repo, _ = source
    state = tmp_path / "node"
    state.mkdir()
    old = WaitingChild()
    events = []
    writes = []

    class OwnedChild:
        pid = 123
        stdin = None
        returncode = None

        def terminate(self):
            events.append("terminate")
            self.returncode = 0

        def wait(self, timeout=None):
            assert self.returncode == 0
            return 0

        def poll(self):
            return self.returncode

    def start(*args, **kwargs):
        events.append("start")
        return OwnedChild()

    def write(*args):
        writes.append(args)
        if len(writes) == 1:
            raise OSError("record write refused")

    monkeypatch.setattr(node_supervise, "start_process", start)
    monkeypatch.setattr(node_supervise, "write_json", write)
    monkeypatch.setattr(node_supervise, "resolve", lambda _: (tmp_path / "new", "new"))
    monkeypatch.setattr(node_supervise, "_program", lambda binary, *args: binary)
    monkeypatch.setattr(node_supervise, "_admitted", lambda *args: None)
    monkeypatch.setattr(node_supervise, "running", lambda _: {"binary": str(tmp_path / "old"), "sha256": "old"})
    monkeypatch.setattr(node_supervise, "point", lambda *args: None)
    monkeypatch.setattr(node_supervise.build_update, "node_args", lambda: [])
    monkeypatch.setattr(old, "terminate", lambda: None, raising=False)
    monkeypatch.setattr(old, "wait", lambda **kwargs: 0)
    monkeypatch.setattr(node_supervise, "_ready", lambda *args: True)
    result = node_supervise._cutover(old, state, [], (None, node_supervise._identity(state)), lambda: False)
    assert result is not None
    assert events == ["start", "terminate", "start"]
    assert len(writes) == 2


def test_observer_recovery_uses_current_restored_node_instead_of_failed_previous(tmp_path, monkeypatch):
    state = tmp_path / "node"
    old = tmp_path / "old" / "node" / "poolhouse-node"
    points = []
    monkeypatch.setattr(node_supervise, "running", lambda _: {"binary": str(old), "sha256": "old",
                                                           "previous": {"binary": "broken", "sha256": "new"}})
    monkeypatch.setattr(runtime_store, "read_state", lambda: {"checkout": str(tmp_path / "source")})
    monkeypatch.setattr(node_supervise, "_admitted", lambda *args: None)
    monkeypatch.setattr(node_supervise, "point", lambda *args: points.append(args))
    assert node_launch._rollback_pointer(state, {}, "new") == (old, "old")
    assert points == [(state, old, "old")]

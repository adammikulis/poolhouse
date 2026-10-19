"""A runtime cutover waits a bounded time for the boundary and names who holds it."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_runtime_trust import commit, git

from poolhouse import node_launch, runtime, runtime_cli, runtime_deploy, runtime_git
from poolhouse.lock import Busy, held_by, only_one, take

pytest_plugins = ["test_runtime_cutover"]


class Holder:
    """Holds the boundary lock of a repository from this process until released."""

    def __init__(self, repo) -> None:
        self.stack = ExitStack()
        self.path = runtime_git.boundary(repo)
        self.stack.enter_context(only_one(self.path, wait=False, note="a landing"))

    def release(self) -> None:
        self.stack.close()


@pytest.fixture
def cutover(source, monkeypatch):
    """A process holding the boundary, a build that would cut over once it is free, and the selections that cutover makes."""
    repo, head = source
    built, selected = [], []

    def builder(plan, stage, prefix):
        prefix.mkdir(parents=True)
        built.append(prefix)
        return runtime.Runtime(prefix, plan.commit, "0", runtime.identity())

    monkeypatch.setattr(runtime, "publish", lambda item: selected.append(item))
    monkeypatch.setattr(runtime_deploy, "acting", lambda _plan: None)
    monkeypatch.setattr(runtime_deploy, "smoke", lambda *_args: None)
    holder = Holder(repo)
    yield SimpleNamespace(holder=holder, plan=runtime_deploy.Plan(repo, head, None), builder=builder, built=built, selected=selected)
    holder.release()


@pytest.fixture
def asked_of_the_lock(monkeypatch):
    """The (wait, timeout) of every lock the boundary takes; each call is refused at once."""
    asked = []

    def refuse(path, *, wait=True, timeout=0.0, **_):
        asked.append((wait, timeout))
        raise Busy(f"{path} is held")

    monkeypatch.setattr(runtime_git, "only_one", refuse)
    return asked


def prepared(cutover, tmp_path):
    return runtime.Runtime(tmp_path / "runtime", cutover.plan.commit, "0", runtime.identity())


@pytest.mark.parametrize("wait_s", [0.0, 3.0])
def test_a_cutover_refuses_a_held_boundary_at_once_and_names_the_holder(cutover, tmp_path, wait_s):
    began = time.monotonic()
    with pytest.raises(Busy) as refused:
        runtime_deploy.switch(prepared(cutover, tmp_path), replace(cutover.plan, boundary_wait_s=wait_s))
    assert str(refused.value) == f"{cutover.holder.path} is held by pid {os.getpid()} a landing"
    assert time.monotonic() - began < 2 and cutover.selected == []


def test_a_build_waits_for_a_landing_that_finishes_and_holds_nothing_meanwhile(cutover):
    root = runtime_deploy.prepare_root()
    seen = []
    watcher = threading.Timer(0.15, lambda: seen.append((held_by(root / "deploy.lock"), list(cutover.built))))
    release = threading.Timer(0.5, cutover.holder.release)
    watcher.start(), release.start()
    outcome = runtime_deploy.ensure(replace(cutover.plan, boundary_wait_s=30), builder=cutover.builder)
    watcher.join(), release.join()
    assert outcome.action == "switched" and len(cutover.selected) == 1 and len(cutover.built) == 1
    assert seen == [("", [])]


def test_a_build_that_outwaits_its_bound_builds_nothing_and_names_the_holder_and_the_wait(cutover):
    outcome = runtime_deploy.ensure(replace(cutover.plan, boundary_wait_s=1), builder=cutover.builder)
    assert outcome.action == "busy" and cutover.built == [] and cutover.selected == []
    assert outcome.detail == f"{cutover.holder.path} is held by pid {os.getpid()} a landing; still held after waiting 1s"


@pytest.mark.parametrize("asked", [1e9, float("inf")])
def test_a_wait_longer_than_the_ceiling_is_cut_to_the_ceiling(source, asked_of_the_lock, asked):
    repo, _ = source
    with pytest.raises(Busy, match="still held after waiting 900s"), runtime_git.crossing(repo, wait_s=asked):
        pytest.fail("entered a boundary that was refused")
    assert asked_of_the_lock == [(True, 900.0)]


@pytest.mark.parametrize("asked", [0.0, -5.0, float("nan")])
def test_a_missing_negative_or_undefined_wait_does_not_wait(source, asked_of_the_lock, asked):
    repo, _ = source
    with pytest.raises(Busy, match=r"is held by nobody$"), runtime_git.crossing(repo, wait_s=asked):
        pytest.fail("entered a boundary that was refused")
    assert asked_of_the_lock == [(False, 0.0)]


@pytest.mark.parametrize("asked", [-5.0, float("nan")])
def test_a_build_with_a_negative_or_undefined_wait_does_not_wait_for_the_boundary(cutover, asked):
    began = time.monotonic()
    outcome = runtime_deploy.ensure(replace(cutover.plan, boundary_wait_s=asked), builder=cutover.builder)
    assert outcome.action == "busy" and "held by pid" in outcome.detail and "waiting" not in outcome.detail
    assert time.monotonic() - began < 2 and cutover.selected == []


def test_the_boundary_waits_are_two_minutes_by_default_and_at_most_fifteen():
    assert (runtime_git.BOUNDARY_WAIT_S, runtime_git.BOUNDARY_WAIT_MAX_S) == (120.0, 900.0)


def test_a_holder_record_is_shown_as_printable_text_only(tmp_path):
    path = tmp_path / "held.lock"
    with only_one(path, note="line\nbreak \x1b[31mred\x1b[0m " + "x" * 400):
        shown = runtime_git.holder(path)
    assert shown.isprintable() and "\n" not in shown and "\x1b" not in shown
    assert shown.startswith(f"pid {os.getpid()} line?break ?[31mred?[0m ") and len(shown) <= 200
    assert runtime_git.holder(path) == "nobody"


def test_a_holder_record_naming_another_process_never_shows_that_process(tmp_path):
    path = tmp_path / "held.lock"
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "--hf-token=hf_SECRET"])
    handle = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        assert take(handle)
        os.write(handle, f"pid {other.pid} landing".encode())
        assert runtime_git.holder(path) == f"pid {other.pid} landing"
    finally:
        other.kill()
        other.wait(timeout=10)
        os.close(handle)


def test_ensure_carries_its_boundary_wait_into_the_plan_and_the_node_swap(source, monkeypatch):
    repo, _ = source
    git(repo, "update-ref", "refs/remotes/origin/dev", commit(repo, "src/poolhouse/__init__.py", ""))
    plans, swaps = [], []
    monkeypatch.setattr(runtime_cli.runtime_deploy, "ensure", lambda plan, **kw: runtime_deploy.Outcome("switched", plan.commit))
    monkeypatch.setattr(runtime_cli, "_audited", lambda *a, **k: None)
    monkeypatch.setattr(runtime_cli.runtime_board, "announce", lambda *a, **k: None)
    monkeypatch.setattr(node_launch, "swap", lambda **kw: swaps.append(kw) or {"action": "idle", "detail": ""})
    monkeypatch.setattr(runtime_cli, "_launchers", lambda named: None)
    monkeypatch.setattr(runtime_cli.runtime_trust, "authorize", lambda command: "person")
    for words in ([], ["--boundary-wait", "7"]):
        assert runtime_cli.main(["ensure", "--now", "--no-update", "--checkout", str(repo), *words]) == 0
        plans.append(swaps[-1]["boundary_wait_s"])
    assert plans == [runtime_cli.runtime_git.BOUNDARY_WAIT_S, 7.0]


def test_the_authority_is_asked_again_after_the_boundary_wait_and_before_anything_is_built(cutover):
    asked = []

    def revoked():
        asked.append("after the wait")
        raise PermissionError("authority revoked")

    threading.Timer(0.3, cutover.holder.release).start()
    with pytest.raises(PermissionError, match="revoked"):
        runtime_deploy.ensure(replace(cutover.plan, boundary_wait_s=10), builder=cutover.builder, after_wait=revoked)
    assert asked == ["after the wait"] and cutover.built == [] and cutover.selected == []

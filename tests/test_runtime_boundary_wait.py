"""A runtime cutover waits a bounded time for the boundary and names who holds it."""

from __future__ import annotations

import os
import threading
import time
from contextlib import ExitStack
from dataclasses import replace

import pytest
from test_runtime_trust import commit, git

from poolhouse import node_launch, runtime, runtime_cli, runtime_deploy, runtime_git
from poolhouse.lock import Busy, only_one

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
def cutover(source, tmp_path, monkeypatch):
    """A process holding the boundary, the cutover it blocks and the selections that cutover makes."""
    repo, head = source
    chosen = runtime.Runtime(tmp_path / "runtime", head, "0", runtime.identity())
    selected = []
    monkeypatch.setattr(runtime, "publish", lambda item: selected.append(item))
    holder = Holder(repo)
    yield holder, chosen, runtime_deploy.Plan(repo, head, None), selected
    holder.release()


def test_a_cutover_refuses_a_held_boundary_at_once_and_names_the_holder(cutover):
    _, chosen, plan, selected = cutover
    began = time.monotonic()
    with pytest.raises(Busy, match=rf"held by pid {os.getpid()} a landing \("):
        runtime_deploy.switch(chosen, plan)
    assert time.monotonic() - began < 2 and selected == []


def test_a_cutover_waits_for_a_landing_that_finishes(cutover):
    holder, chosen, plan, selected = cutover
    threading.Timer(0.4, holder.release).start()
    runtime_deploy.switch(chosen, replace(plan, boundary_wait_s=30))
    assert selected == [chosen]


def test_a_cutover_that_outwaits_its_bound_names_the_holder_and_the_wait(cutover):
    _, chosen, plan, selected = cutover
    with pytest.raises(Busy, match=rf"held by pid {os.getpid()}.*still held after waiting 1s"):
        runtime_deploy.switch(chosen, replace(plan, boundary_wait_s=1))
    assert selected == []


@pytest.mark.parametrize("asked", [1e9, float("inf")])
def test_a_wait_longer_than_the_ceiling_is_cut_to_the_ceiling(cutover, monkeypatch, asked):
    _, chosen, plan, selected = cutover
    monkeypatch.setattr(runtime_git, "BOUNDARY_WAIT_MAX_S", 1.0)
    began = time.monotonic()
    with pytest.raises(Busy, match="still held after waiting 1s"):
        runtime_deploy.switch(chosen, replace(plan, boundary_wait_s=asked))
    assert time.monotonic() - began < 10 and selected == []


@pytest.mark.parametrize("asked", [-5.0, float("nan")])
def test_a_negative_or_undefined_wait_does_not_wait(cutover, asked):
    _, chosen, plan, selected = cutover
    began = time.monotonic()
    with pytest.raises(Busy, match="held by pid"):
        runtime_deploy.switch(chosen, replace(plan, boundary_wait_s=asked))
    assert time.monotonic() - began < 2 and selected == []


def test_a_holder_record_is_shown_as_printable_text_only(tmp_path):
    path = tmp_path / "held.lock"
    with only_one(path, note="line\nbreak \x1b[31mred\x1b[0m " + "x" * 400):
        shown = runtime_git.holder(path)
    assert shown.isprintable() and "\n" not in shown and "\x1b" not in shown
    assert shown.startswith(f"pid {os.getpid()} line?break ?[31mred?[0m ") and len(shown) < 400
    assert runtime_git.holder(path) == "nobody"


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

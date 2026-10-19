"""A runtime refresh for a tip origin does not hold yet waits for the push; a commit that is not on the development branch is still refused."""

from __future__ import annotations

import threading
import time

import pytest
from test_runtime_trust import commit, git

from poolhouse import runtime_await, runtime_cli, runtime_deploy, runtime_source
from poolhouse.lock import only_one

pytest_plugins = ["test_runtime_trust"]


@pytest.fixture
def unpublished(world, monkeypatch):
    """The world's repository with one commit on `dev` that `origin/dev` does not hold, and the spawned argv recorded."""
    tip = commit(world.repo, "unpushed.txt")
    started = []
    monkeypatch.setattr(runtime_cli, "_start_background", lambda argv: started.append(argv))
    monkeypatch.setattr(runtime_await, "POLL_S", 0.02)
    return world, tip, started


def publish(world, sha):
    git(world.repo, "update-ref", "refs/remotes/origin/dev", sha)


def ensure_background(world, *words):
    return runtime_cli.main(["ensure", "--background", "--checkout", str(world.repo), *words])


def test_admission_tells_a_local_only_commit_from_one_that_is_not_on_the_branch(unpublished):
    world, tip, _ = unpublished
    with pytest.raises(runtime_source.Unpublished, match="published at origin/dev"):
        runtime_deploy.admit(world.repo, tip)
    with pytest.raises(runtime_deploy.DeployError, match="published at origin/dev") as refused:
        runtime_deploy.admit(world.repo, world.evil)
    assert not isinstance(refused.value, runtime_source.Unpublished)
    publish(world, tip)
    runtime_deploy.admit(world.repo, tip)


def test_a_background_refresh_of_an_unpublished_tip_starts_one_waiter_and_builds_nothing(unpublished, monkeypatch, capsys):
    world, _, started = unpublished
    monkeypatch.setattr(runtime_deploy, "ensure", lambda *a, **k: pytest.fail("built an unpublished tip"))
    assert ensure_background(world) == 0
    [argv] = started
    assert argv[0] == "ensure" and argv[argv.index("--await-published") + 1] == str(runtime_await.AWAIT_S)
    assert "--background" not in argv and "background build starts when the push arrives" in capsys.readouterr().out
    with only_one(runtime_deploy.prepare_root() / runtime_await.LOCK, note="waiting"):
        assert ensure_background(world) == 0
    assert len(started) == 1 and "already waiting for a push" in capsys.readouterr().out


def test_a_background_refresh_of_a_commit_off_the_branch_is_refused_and_waits_for_nothing(unpublished, capsys):
    world, _, started = unpublished
    assert ensure_background(world, "--ref", world.evil) == 1
    assert started == [] and "not on the development branch" in capsys.readouterr().err


def build_after_push(world, tip, monkeypatch, seconds, delay):
    """Run the waiter command with `delay` seconds before origin holds `tip`; the plans it would build."""
    plans = []
    monkeypatch.setattr(runtime_cli, "_run", lambda args, plan: plans.append(plan) or runtime_deploy.Outcome("current", plan.commit))
    if delay is not None:
        threading.Timer(delay, publish, (world, tip)).start()
    code = runtime_cli.main(["ensure", "--now", "--no-update", "--await-published", str(seconds), "--checkout", str(world.repo)])
    return code, plans


def test_the_waiter_builds_the_tip_once_origin_holds_it(unpublished, monkeypatch):
    world, tip, _ = unpublished
    began = time.monotonic()
    code, plans = build_after_push(world, tip, monkeypatch, 30, 0.3)
    assert code == 0 and [plan.commit for plan in plans] == [tip]
    assert time.monotonic() - began < 10


def test_the_waiter_builds_nothing_when_the_push_never_comes(unpublished, monkeypatch, capsys):
    world, _, _ = unpublished
    code, plans = build_after_push(world, "", monkeypatch, 0.3, None)
    assert code == 1 and plans == [] and "did not reach origin within" in capsys.readouterr().err


def test_a_second_waiter_leaves_the_wait_to_the_first(unpublished, monkeypatch, capsys):
    world, _, _ = unpublished
    with only_one(runtime_deploy.prepare_root() / runtime_await.LOCK, note="waiting"):
        code, plans = build_after_push(world, "", monkeypatch, 5, None)
    assert code == 0 and plans == [] and "already waiting for a push" in capsys.readouterr().out


def test_a_waiter_for_a_commit_off_the_branch_is_refused_without_waiting(unpublished, monkeypatch, capsys):
    world, _, _ = unpublished
    monkeypatch.setattr(runtime_await, "until_published", lambda *a, **k: pytest.fail("waited for a commit off the branch"))
    began = time.monotonic()
    code = runtime_cli.main(["ensure", "--now", "--no-update", "--await-published", "30", "--ref", world.evil, "--checkout", str(world.repo)])
    assert code == 1 and time.monotonic() - began < 10 and "not on the development branch" in capsys.readouterr().err


@pytest.mark.usefixtures("unpublished")
def test_a_waiter_for_another_repository_is_refused_without_waiting(monkeypatch, tmp_path, capsys):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    git(foreign, "init", "-q", "-b", "dev")
    commit(foreign, "src/poolhouse/__init__.py", "")
    monkeypatch.setattr(runtime_await, "until_published", lambda *a, **k: pytest.fail("waited in a repository that was not admitted"))
    code = runtime_cli.main(["ensure", "--now", "--no-update", "--await-published", "30", "--checkout", str(foreign)])
    refusal = capsys.readouterr().err
    assert code == 1 and ("recorded repository" in refusal or "no source repository is recorded" in refusal)


@pytest.mark.parametrize("asked", [1e9, float("inf"), float("nan")])
def test_a_wait_for_a_push_is_cut_to_the_ceiling(unpublished, monkeypatch, asked):
    world, tip, _ = unpublished
    now = [0.0]
    monkeypatch.setattr(runtime_await, "AWAIT_S", 5.0)
    monkeypatch.setattr(runtime_await, "POLL_S", 1.0)
    slept = []
    assert not runtime_await.until_published(world.repo, tip, asked, clock=lambda: now[0],
                                             sleep=lambda seconds: (slept.append(seconds), now.__setitem__(0, now[0] + seconds)))
    assert 5.0 <= now[0] < 5.0 + runtime_await.POLL_S + 1 and slept


def test_a_negative_wait_for_a_push_does_not_wait(unpublished):
    world, tip, _ = unpublished
    assert not runtime_await.until_published(world.repo, tip, -3.0, sleep=lambda seconds: pytest.fail("slept"))


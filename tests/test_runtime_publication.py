"""A runtime refresh for a tip origin does not hold yet waits for the push; a commit that is not on the development branch is still refused."""

from __future__ import annotations

import stat
import threading
import time
from pathlib import Path

import pytest
from test_runtime_trust import commit, git

from poolhouse import authority, runtime, runtime_await, runtime_cli, runtime_deploy, runtime_source
from poolhouse.lock import Busy, only_one
from poolhouse.person import HumanRequired

pytest_plugins = ["test_runtime_trust"]


@pytest.fixture
def unpublished(world, monkeypatch):
    """The world's repository with one commit on `dev` that `origin/dev` does not hold, and the spawned argv recorded."""
    tip = commit(world.repo, "unpushed.txt")
    started = []
    monkeypatch.setattr(runtime_cli, "_start_background", lambda argv: started.append(argv))
    monkeypatch.setattr(runtime_await, "POLL_S", 0.02)
    return world, tip, started


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))


class Ticks:
    """A clock that moves only when it sleeps; a sleep beyond `limit` fails the test instead of waiting on."""

    def __init__(self, limit: int = 500) -> None:
        self.now, self.sleeps, self.limit = 0.0, 0, limit

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps += 1
        if self.sleeps > self.limit:
            pytest.fail(f"still waiting after {self.now:.0f}s and {self.sleeps} sleeps")
        self.now += seconds


def never_published():
    raise runtime_source.Unpublished("c0ffee1 is not on the development branch published at origin/dev", Path.cwd(), "c" * 40)


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


def waiter(world, monkeypatch, seconds):
    """Run the waiter command; its exit code and the plans it would build."""
    plans = []
    monkeypatch.setattr(runtime_cli, "_run", lambda args, plan: plans.append(plan) or runtime_deploy.Outcome("current", plan.commit))
    code = runtime_cli.main(["ensure", "--now", "--no-update", "--await-published", str(seconds), "--checkout", str(world.repo)])
    return code, plans


def build_after_push(world, tip, monkeypatch, seconds, delay):
    """Run the waiter command with `delay` seconds before origin holds `tip`; the plans it would build."""
    if delay is not None:
        threading.Timer(delay, publish, (world, tip)).start()
    return waiter(world, monkeypatch, seconds)


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


def test_the_waiter_follows_the_branch_when_it_moves_while_it_waits(unpublished, monkeypatch):
    world, first, _ = unpublished
    moved = []

    def move():
        time.sleep(0.2)
        moved.append(commit(world.repo, "second.txt"))
        time.sleep(0.3)
        publish(world, first)
        time.sleep(0.3)
        publish(world, moved[0])

    mover = threading.Thread(target=move)
    mover.start()
    code, plans = waiter(world, monkeypatch, 30)
    mover.join()
    assert code == 0 and [plan.commit for plan in plans] == moved


def test_authority_is_checked_again_after_the_wait_and_before_anything_is_built(unpublished, monkeypatch, capsys):
    world, tip, _ = unpublished
    seen = []

    def authorize(command):
        seen.append(git(world.repo, "rev-parse", "refs/remotes/origin/dev"))
        if len(seen) > 1:
            raise HumanRequired("runtime ensure is a person's again")
        threading.Timer(0.5, publish, (world, tip)).start()
        return authority.PERSON

    monkeypatch.setattr(runtime_cli.runtime_trust, "authorize", authorize)
    code, plans = waiter(world, monkeypatch, 30)
    assert code == 1 and plans == [] and "a person's again" in capsys.readouterr().err
    assert len(seen) == 2 and seen[0] != tip and seen[1] == tip


def test_a_second_waiter_is_told_that_nothing_was_built_and_leaves_the_wait_to_the_first(unpublished, monkeypatch, capsys):
    world, _, _ = unpublished
    with only_one(runtime_deploy.prepare_root() / runtime_await.LOCK, note="waiting"):
        code, plans = build_after_push(world, "", monkeypatch, 5, None)
    refusal = capsys.readouterr().err
    assert code == 1 and plans == [] and "not built" in refusal and "already waiting for a push" in refusal


def test_a_waiter_for_a_commit_off_the_branch_is_refused_without_waiting(unpublished, monkeypatch, capsys):
    world, _, _ = unpublished
    monkeypatch.setattr(runtime_await, "following", lambda *a, **k: pytest.fail("waited for a commit off the branch"))
    began = time.monotonic()
    code = runtime_cli.main(["ensure", "--now", "--no-update", "--await-published", "30", "--ref", world.evil, "--checkout", str(world.repo)])
    assert code == 1 and time.monotonic() - began < 10 and "not on the development branch" in capsys.readouterr().err


@pytest.mark.usefixtures("unpublished")
def test_a_waiter_for_another_repository_is_refused_without_waiting(monkeypatch, tmp_path, capsys):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    git(foreign, "init", "-q", "-b", "dev")
    commit(foreign, "src/poolhouse/__init__.py", "")
    monkeypatch.setattr(runtime_await, "following", lambda *a, **k: pytest.fail("waited in a repository that was not admitted"))
    code = runtime_cli.main(["ensure", "--now", "--no-update", "--await-published", "30", "--checkout", str(foreign)])
    refusal = capsys.readouterr().err
    assert code == 1 and ("recorded repository" in refusal or "no source repository is recorded" in refusal)


def test_the_wait_for_a_push_is_fifteen_minutes_at_most():
    assert (runtime_await.AWAIT_S, runtime_await.POLL_S) == (900.0, 2.0)


@pytest.mark.usefixtures("home")
@pytest.mark.parametrize("asked", [1e9, float("inf"), float("nan")])
def test_a_wait_for_a_push_is_cut_to_the_ceiling(asked):
    ticks = Ticks()
    with pytest.raises(runtime_deploy.DeployError, match="did not reach origin within 900s"):
        runtime_await.following(never_published, asked, sleep=ticks.sleep, clock=ticks.clock)
    assert 900.0 <= ticks.now < 900.0 + runtime_await.POLL_S


@pytest.mark.usefixtures("home")
@pytest.mark.parametrize("asked", [-3.0, 0.0])
def test_a_negative_wait_for_a_push_does_not_wait(asked):
    ticks = Ticks(limit=0)
    with pytest.raises(runtime_deploy.DeployError, match="did not reach origin within 0s"):
        runtime_await.following(never_published, asked, sleep=ticks.sleep, clock=ticks.clock)


@pytest.mark.usefixtures("home")
def test_a_wait_ends_with_the_plan_the_moment_the_tip_is_published():
    ticks, answers = Ticks(), [never_published, never_published, lambda: "plan"]
    assert runtime_await.following(lambda: answers.pop(0)(), 60, sleep=ticks.sleep, clock=ticks.clock) == "plan"
    assert ticks.sleeps == 2


@pytest.mark.usefixtures("home")
def test_only_one_process_waits_for_a_push_at_a_time():
    with only_one(runtime_deploy.prepare_root() / runtime_await.LOCK, note="waiting"), pytest.raises(Busy):
        runtime_await.following(never_published, 60, sleep=lambda _: pytest.fail("slept"))


def test_asking_who_waits_creates_neither_the_runtime_directory_nor_its_lock(home):
    assert runtime_await.waiting() == "" and not runtime.directory().exists()
    root = runtime_deploy.prepare_root()
    assert stat.S_IMODE(root.stat().st_mode) == 0o700 and not (root / runtime_await.LOCK).exists()

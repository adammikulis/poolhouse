"""Runs with the same arguments and tree follow one job, a follower's cancel leaves it for the others, and a cancel marks the job first."""

# ruff: noqa: F811
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_testjobs import table  # noqa: F401  (fixture)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import testjobs  # noqa: E402
import testreuse_store as storage  # noqa: E402

pytestmark = pytest.mark.slow

ARGV = ["tests/x.py", "60", "0"]


def start_following(table, argv, tree, pid, owner="alice"):
    """Start a run that follows its job from the process ``pid``; the job id and whether the run joined a job."""
    jobs, script, root = table
    follower = {"pid": pid, "started": storage.process_start(pid), "owner": owner}
    return jobs.start(argv, {"id": owner}, root, script, {"key": {"argv": argv, "tree": tree}, "follower": follower})


def sleepers(count):
    return [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"]) for _ in range(count)]


def stop_all(jobs, processes):
    for job in jobs.ids():
        jobs.cancel(job, "alice")
    for process in processes:
        process.kill()
        process.wait()


def running(jobs, job):
    deadline = time.monotonic() + 20
    while jobs.state(job).get("state") != "running" and time.monotonic() < deadline:
        time.sleep(0.1)
    assert jobs.state(job).get("state") == "running"


def test_a_run_with_the_same_arguments_and_tree_follows_the_job_that_is_queued_or_running(table):
    jobs, _, _ = table
    one, two = sleepers(2)
    try:
        first, joined = start_following(table, ARGV, "tree-a", one.pid)
        again, joined_again = start_following(table, ARGV, "tree-a", two.pid, owner="bob")
        assert (joined, joined_again, again) == (False, True, first)
        assert len(jobs.followers(first)) == 2 and len(jobs.ids()) == 1
    finally:
        stop_all(jobs, (one, two))


def test_a_different_tree_or_different_arguments_never_follows(table):
    jobs, _, _ = table
    holders = sleepers(4)
    try:
        base, _ = start_following(table, ARGV, "tree-a", holders[0].pid)
        others = [start_following(table, ARGV, "tree-b", holders[1].pid),
                  start_following(table, ["tests/y.py", "60", "0"], "tree-a", holders[2].pid),
                  start_following(table, [*ARGV, "--no-reuse"], "tree-a", holders[3].pid)]
        assert all(joined is False and job != base for job, joined in others)
        assert len({base, *(job for job, _ in others)}) == 4
    finally:
        stop_all(jobs, holders)


def test_a_job_with_no_known_tree_is_never_followed(table):
    jobs, _, _ = table
    one, two = sleepers(2)
    try:
        first, _ = start_following(table, ARGV, None, one.pid)
        second, joined = start_following(table, ARGV, None, two.pid)
        assert joined is False and second != first
    finally:
        stop_all(jobs, (one, two))


def test_a_cancelled_or_failed_job_is_never_followed(table):
    jobs, _, _ = table
    one, two, three = sleepers(3)
    try:
        first, _ = start_following(table, ARGV, "tree-a", one.pid)
        running(jobs, first)
        assert jobs.cancel(first, "alice").endswith("cancelled")
        second, joined = start_following(table, ARGV, "tree-a", two.pid)
        assert joined is False and second != first
        jobs.write(second, "status.json", {"state": "failed", "exit": 1, "finished": time.time()})
        third, joined = start_following(table, ARGV, "tree-a", three.pid)
        assert joined is False and third not in (first, second)
    finally:
        stop_all(jobs, (one, two, three))


def test_one_followers_cancel_leaves_the_job_for_the_others_and_the_last_followers_cancel_stops_it(table):
    jobs, _, _ = table
    one, two = sleepers(2)
    try:
        job, _ = start_following(table, ARGV, "tree-a", one.pid)
        start_following(table, ARGV, "tree-a", two.pid, owner="bob")
        running(jobs, job)
        assert "left job" in jobs.cancel(job, "bob", leaving=two.pid)
        assert jobs.state(job)["state"] == "running" and len(jobs.followers(job)) == 1
        assert jobs.cancel(job, "alice", leaving=one.pid).endswith("cancelled")
        assert jobs.wait(job, 20)["state"] == "cancelled"
    finally:
        stop_all(jobs, (one, two))


def test_a_cancel_that_names_no_follower_stops_the_job_for_every_follower(table):
    jobs, _, _ = table
    one, two = sleepers(2)
    try:
        job, _ = start_following(table, ARGV, "tree-a", one.pid)
        start_following(table, ARGV, "tree-a", two.pid, owner="bob")
        running(jobs, job)
        assert jobs.cancel(job, "carol").endswith("cancelled")
        assert jobs.wait(job, 20)["state"] == "cancelled"
    finally:
        stop_all(jobs, (one, two))


def test_a_follower_that_died_without_leaving_is_not_counted(table):
    jobs, _, _ = table
    one, two = sleepers(2)
    try:
        job, _ = start_following(table, ARGV, "tree-a", one.pid)
        start_following(table, ARGV, "tree-a", two.pid, owner="bob")
        two.kill()
        two.wait()
        assert [f["owner"] for f in jobs.followers(job)] == ["alice"]
        running(jobs, job)
        assert jobs.cancel(job, "alice", leaving=one.pid).endswith("cancelled")
    finally:
        stop_all(jobs, (one, two))


def test_a_job_that_cannot_be_ended_goes_back_to_its_earlier_state_and_no_run_joins_it_meanwhile(table, monkeypatch):
    jobs, _, root = table
    runner, other = sleepers(2)
    seen = []
    key = {"argv": ARGV, "tree": "tree-a"}
    job = "20261016-000000-aaaa"
    jobs.write(job, "spec.json", {"id": job, "argv": ARGV, "owner": {"id": "alice"}, "created": time.time(), "secret": "s", "key": key,
                                  "root": str(root)})
    jobs.write(job, "status.json", {"state": "running", "at": time.time(), "pid": runner.pid, "started": storage.process_start(runner.pid)})

    def stubborn(self, job, pid, started):
        seen.append((self.state(job)["state"], self.matching(key)))
        raise OSError("still running")

    monkeypatch.setattr(testjobs.Jobs, "reap", stubborn)
    try:
        with pytest.raises(OSError, match="still running"):
            jobs.cancel(job, "alice")
        assert seen == [("cancelling", "")] and jobs.state(job)["state"] == "running"
        assert start_following(table, ARGV, "tree-a", other.pid) == (job, True)
    finally:
        for process in (runner, other):
            process.kill()
            process.wait()


def test_a_job_left_cancelling_for_longer_than_the_resend_time_is_cancelled_again(table, monkeypatch):
    jobs, _, _ = table
    one, = sleepers(1)
    try:
        job, _ = start_following(table, ARGV, "tree-a", one.pid)
        running(jobs, job)
        jobs.write(job, "status.json", {**jobs.state(job), "state": "cancelling", "was": "running", "at": time.time()})
        assert jobs.cancel(job, "alice") == f"job {job} is already being cancelled"
        monkeypatch.setattr(testjobs, "RESEND_S", 0.0)
        assert jobs.cancel(job, "alice").endswith("cancelled")
    finally:
        stop_all(jobs, (one,))


def test_a_runner_that_starts_after_a_cancel_keeps_the_cancelling_mark(table):
    jobs, _, _ = table
    job = "20261016-000000-aaaa"
    jobs.write(job, "status.json", {"state": "cancelling", "at": time.time()})
    jobs.mark_running(job, {"pid": 7})
    assert jobs.read(job)["state"] == "cancelling" and jobs.read(job)["pid"] == 7
    jobs.write(job, "status.json", {"state": "queued", "at": time.time()})
    jobs.mark_running(job, {"pid": 7})
    assert jobs.read(job)["state"] == "running"


def test_the_runner_of_a_job_stops_when_its_last_follower_has_gone(table, monkeypatch):
    jobs, _, _ = table
    one, = sleepers(1)
    sent, real = [], os.kill

    def kill(pid, number):
        if pid == os.getpid() and number == signal.SIGTERM:
            sent.append(number)
        else:
            real(pid, number)

    monkeypatch.setattr(testjobs, "WATCH_S", 0.05)
    monkeypatch.setattr(os, "kill", kill)
    try:
        job, _ = start_following(table, ARGV, "tree-a", one.pid)
        jobs.watch_follower(job)
        time.sleep(0.3)
        assert sent == []
        one.kill()
        one.wait()
        deadline = time.monotonic() + 10
        while not sent and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sent == [signal.SIGTERM]
    finally:
        stop_all(jobs, (one,))


def test_a_run_that_joined_a_job_is_told_when_the_job_s_tree_is_not_the_tree_it_joined(table, monkeypatch, capsys):
    jobs, _, root = table
    monkeypatch.setattr(jobs, "start", lambda *args: ("job", True))
    monkeypatch.setattr(jobs, "wait", lambda *args, **kwargs: {"state": "done", "exit": 0, "tree": "tree-b"})
    owner = {"id": "alice"}
    assert jobs.follow(ARGV, owner, root, root / "runner.py", {"key": {"argv": ARGV, "tree": "tree-a"}}) == 4
    assert "does not describe this run's tree" in capsys.readouterr().err
    assert jobs.follow(ARGV, owner, root, root / "runner.py", {"key": {"argv": ARGV, "tree": "tree-b"}}) == 0


def test_a_job_records_its_tree_in_its_key_so_a_later_run_can_join_it(table):
    jobs, _, _ = table
    one, two = sleepers(2)
    try:
        job, _ = start_following(table, ARGV, None, one.pid)
        jobs.record_tree(job, "tree-a")
        assert jobs.read(job, "spec.json")["key"] == {"argv": ARGV, "tree": "tree-a"}
        assert start_following(table, ARGV, "tree-a", two.pid) == (job, True)
    finally:
        stop_all(jobs, (one, two))

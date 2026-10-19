"""The processes a confined run starts are followed from the moment it began."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator

import pytest

from poolhouse.sandbox import process_tree


@pytest.fixture(autouse=True)
def macos():
    """These tests need the kqueue and libproc of macOS."""
    if sys.platform != "darwin":
        pytest.skip("processes are followed with kqueue and libproc")


@pytest.fixture
def shell() -> Iterator[Callable[[str], tuple[subprocess.Popen, process_tree.Tree]]]:
    """Starts a shell script that prints the pid of each process it starts, and follows it."""
    started: list[subprocess.Popen] = []

    def start(script: str) -> tuple[subprocess.Popen, process_tree.Tree]:
        proc = subprocess.Popen(["/bin/sh", "-c", script], stdout=subprocess.PIPE, text=True, start_new_session=True)
        started.append(proc)
        return proc, process_tree.follow(proc.pid)

    yield start
    for proc in started:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        proc.stdout.close()


def printed(proc: subprocess.Popen, count: int) -> list[int]:
    """The first ``count`` pids the script printed."""
    return [int(proc.stdout.readline()) for _ in range(count)]


def followed(tree: process_tree.Tree, pids: list[int], seconds: float = 5.0) -> bool:
    """Whether every pid of ``pids`` is in ``tree`` within ``seconds``."""
    deadline = time.monotonic() + seconds
    while not all(pid in tree for pid in pids):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def test_the_process_and_every_child_it_starts_are_followed(shell):
    proc, tree = shell("sleep 30 & echo $!; sleep 30 & echo $!; wait")
    assert proc.pid in tree and tree.complete
    assert followed(tree, printed(proc, 2))


def test_a_child_of_a_child_is_followed(shell):
    proc, tree = shell("sh -c 'sleep 30 & echo $!; wait' & wait")
    assert followed(tree, printed(proc, 1))


def test_a_process_that_outlives_the_one_that_started_it_is_still_followed(shell):
    proc, tree = shell("(sleep 30 & echo $!; sleep 0.2); sleep 30")
    (orphan,) = printed(proc, 1)
    assert followed(tree, [orphan])
    time.sleep(0.5)
    assert orphan in tree


def test_many_short_lived_children_are_all_followed(shell):
    proc, tree = shell("for n in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do sleep 0.05 & echo $!; done; wait")
    assert followed(tree, printed(proc, 20))


def test_the_processes_stay_in_the_tree_after_they_have_ended(shell):
    proc, tree = shell("sleep 0.1 & echo $!; wait")
    (child,) = printed(proc, 1)
    assert followed(tree, [child])
    proc.wait()
    time.sleep(0.3)
    assert child in tree and proc.pid in tree


def test_a_process_that_none_of_them_started_is_not_in_the_tree(shell):
    _, tree = shell("sleep 30 & wait")
    outsider = subprocess.Popen(["/bin/sleep", "30"])
    try:
        time.sleep(0.3)
        assert outsider.pid not in tree
    finally:
        outsider.kill()
        outsider.wait()


def test_a_process_that_had_ended_before_it_was_followed_is_a_tree_that_is_not_complete():
    proc = subprocess.Popen(["/usr/bin/true"])
    proc.wait()
    tree = process_tree.follow(proc.pid)
    assert not tree.complete and proc.pid in tree


def test_a_run_that_starts_more_processes_than_a_tree_keeps_is_not_complete(shell, monkeypatch):
    monkeypatch.setattr(process_tree, "KEPT", 3)
    _, tree = shell("for n in 1 2 3 4 5; do sleep 30 & done; wait")
    deadline = time.monotonic() + 5
    while tree.complete and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not tree.complete


class Unread(process_tree._Follower):
    """A follower whose reader thread never runs: only `flush` takes in what the kernel reported."""

    def _read(self, queue: object) -> None:
        return


def test_a_process_that_never_forked_is_a_sealed_tree(shell):
    _, tree = shell("exec sleep 30")
    time.sleep(0.3)
    process_tree.flush()
    assert tree.complete and tree.sealed


def test_a_process_that_forked_is_not_a_sealed_tree_when_the_child_has_ended(shell):
    _, tree = shell("sleep 0.05; sleep 30")
    deadline = time.monotonic() + 5
    while tree.sealed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not tree.sealed and tree.complete


def test_a_process_with_a_child_when_it_was_first_looked_at_is_not_a_sealed_tree():
    proc = subprocess.Popen(["/bin/sh", "-c", "sleep 30 & wait"], start_new_session=True)
    try:
        time.sleep(0.4)
        assert not process_tree.follow(proc.pid).sealed
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def test_a_process_that_started_and_reaped_a_child_before_it_was_looked_at_is_not_a_sealed_tree():
    proc = subprocess.Popen(["/bin/sh", "-c", "sleep 0.01; read line"], stdin=subprocess.PIPE, start_new_session=True)
    try:
        time.sleep(0.5)
        tree = process_tree.follow(proc.pid)
        assert tree.complete and not tree.sealed
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        proc.stdin.close()


def test_a_fork_the_reader_has_not_taken_in_is_counted_by_flush():
    follower = Unread()
    proc = subprocess.Popen(["/bin/sh", "-c", "sleep 0.3; sleep 0.01; exec sleep 30"], start_new_session=True)
    try:
        tree = follower.follow(proc.pid)
        time.sleep(0.8)
        assert tree.sealed
        follower.flush()
        assert not tree.sealed
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def test_a_tree_that_has_been_released_is_not_followed_any_more(shell):
    proc, tree = shell("sleep 0.5; sleep 30 & echo $!; wait")
    process_tree.release(tree)
    time.sleep(1.2)
    process_tree.flush()
    assert not followed(tree, printed(proc, 1), 0.1)


def test_a_pid_that_an_ended_process_had_belongs_to_the_tree_for_a_while_only(monkeypatch):
    monkeypatch.setattr(process_tree, "GRACE_S", 0.2)
    tree = process_tree.Tree(100)
    assert tree.add(200)
    tree.ended(200)
    assert 200 in tree and tree.holds_any([200])
    time.sleep(0.3)
    assert 200 not in tree and not tree.holds_any([200]) and 100 in tree


def test_a_pid_that_an_ended_process_had_can_be_taken_again(monkeypatch):
    tree = process_tree.Tree(100)
    assert tree.add(200)
    tree.ended(200)
    assert tree.add(200)
    monkeypatch.setattr(process_tree, "GRACE_S", 0.0)
    time.sleep(0.05)
    assert 200 in tree


def test_a_tree_that_is_full_of_pids_that_have_expired_is_still_complete(monkeypatch):
    monkeypatch.setattr(process_tree, "KEPT", 3)
    monkeypatch.setattr(process_tree, "GRACE_S", 0.0)
    tree = process_tree.Tree(100)
    for pid in (200, 300):
        assert tree.add(pid)
        tree.ended(pid)
    time.sleep(0.05)
    assert tree.add(400) and tree.add(500) and tree.complete


def test_a_process_that_took_the_pid_of_one_that_ended_is_followed_with_its_children(shell, monkeypatch):
    root, tree = shell("sleep 30")
    reused = subprocess.Popen(["/bin/sh", "-c", "sleep 0.5; sleep 30 & echo $!; wait"], stdout=subprocess.PIPE, text=True,
                              start_new_session=True)
    try:
        assert tree.add(reused.pid)
        tree.ended(reused.pid)
        listing = process_tree._FOLLOWER._children
        monkeypatch.setattr(process_tree._FOLLOWER, "_children", lambda pid: [reused.pid] if pid == root.pid else listing(pid))
        process_tree._FOLLOWER._scan(root.pid, tree)
        assert followed(tree, [int(reused.stdout.readline())])
    finally:
        os.killpg(reused.pid, signal.SIGKILL)
        reused.wait()
        reused.stdout.close()

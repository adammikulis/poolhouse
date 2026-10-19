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

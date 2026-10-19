"""A test node whose run is gone is stopped by its supervisor, which watches the process that asked for the node."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil

from poolhouse import node_launch, node_supervise
from poolhouse.lock import pid_alive


def exited_unreaped():
    """A child that has exited and has not been waited for."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.monotonic() + 20
    while psutil.Process(child.pid).status() != psutil.STATUS_ZOMBIE:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    return child


def sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def test_a_process_that_exited_and_was_not_waited_for_is_not_alive():
    zombie = exited_unreaped()
    try:
        assert pid_alive(zombie.pid) is False
        assert node_supervise._until_parent_gone(lambda: False, zombie.pid)() is True
    finally:
        zombie.wait()
    assert pid_alive(os.getpid()) is True


def test_a_supervisor_stops_when_the_process_that_owns_its_node_has_exited():
    owner = sleeper()
    try:
        stopping = node_supervise._until_parent_gone(lambda: False, owner.pid)
        assert stopping() is False
        owner.kill()
        owner.wait()
        assert stopping() is True
    finally:
        owner.kill()
        owner.wait()


def test_a_supervisor_with_no_owner_and_no_stop_request_keeps_running():
    assert node_supervise._until_parent_gone(lambda: False, 0)() is False
    assert node_supervise._until_parent_gone(lambda: True, 0)() is True


def test_a_supervisor_is_started_watching_the_pytest_process_that_asked_for_the_node(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(node_launch.jobs, "detach", lambda module, argv, log: started.append(argv))
    monkeypatch.setenv(node_supervise.PARENT_ENV, str(os.getpid()))
    node_launch._start_supervisor(tmp_path / "node", ["--presence", "terminal"])
    assert started[0][-2:] == ["--parent-pid", str(os.getpid())]
    monkeypatch.delenv(node_supervise.PARENT_ENV)
    started.clear()
    node_launch._start_supervisor(tmp_path / "node", [])
    assert "--parent-pid" not in started[0]


def test_the_devices_own_node_never_watches_a_process_named_by_the_environment(monkeypatch):
    started = []
    monkeypatch.setattr(node_launch.jobs, "detach", lambda module, argv, log: started.append(argv))
    monkeypatch.setenv(node_supervise.PARENT_ENV, "4242")
    node_launch._start_supervisor(node_launch.default_state(), [])
    assert "--parent-pid" not in started[0]


def test_a_hostile_owner_value_that_is_not_a_pid_is_not_passed_on(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(node_launch.jobs, "detach", lambda module, argv, log: started.append(argv))
    monkeypatch.setenv(node_supervise.PARENT_ENV, "1; rm -rf /")
    node_launch._start_supervisor(tmp_path / "node", [])
    assert "--parent-pid" not in started[0]


def test_the_suite_marks_its_own_process_as_the_owner_of_every_node_it_starts():
    assert os.environ[node_supervise.PARENT_ENV] == str(os.getpid())

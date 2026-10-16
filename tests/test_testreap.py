"""A test node whose run is gone is stopped: by its supervisor watching the run's process, and by the runner at its start."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import testreap

from poolhouse import node_launch, node_supervise

SUPERVISOR = "/usr/bin/python3 -m poolhouse.node_launch supervise --state {state} --arg=--presence --arg=terminal"
NODE = "/var/folders/x/T/pytest-of-someone/pytest-3/popen-gw1/node0/poolhouse-node run --state {state} --presence terminal"


def rows(*specs):
    return [(pid, age, template.format(state=state)) for pid, age, template, state in specs]


def test_a_ps_elapsed_time_is_read_in_seconds():
    assert [testreap.seconds(text) for text in ("00:09", "06:23", "01:48:08", "1-12:07:58", "garbage")] == [
        9.0, 383.0, 6488.0, 130_078.0, 0.0]


def test_only_old_test_supervisors_and_nodes_in_a_test_state_folder_are_stale_and_supervisors_come_first():
    old = testreap.MIN_AGE_S + 1
    table = rows((11, old, NODE, "/tmp/mlabc/node"), (12, old, SUPERVISOR, "/private/tmp/mlxyz/node"),
                 (13, 60.0, SUPERVISOR, "/tmp/mlnew/node"),
                 (14, old, SUPERVISOR, "/Users/someone/.poolhouse/node"),
                 (15, old, SUPERVISOR, "/tmp/other/node"),
                 (16, old, "/usr/bin/python3 -m pytest /tmp/mlabc/node", "/tmp/mlabc/node"))
    assert testreap.stale(table) == [12, 11]


def test_a_command_with_no_state_folder_is_never_stale():
    assert testreap.stale([(1, 99999.0, "/usr/bin/python3 -m poolhouse.node_launch supervise")]) == []


def test_a_supervisor_in_a_test_state_folder_is_stopped_and_one_beside_it_is_not():
    mine = Path(tempfile.mkdtemp(prefix="mlreap", dir="/tmp"))
    other = Path(tempfile.mkdtemp(prefix="mlkeep", dir="/tmp"))
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
    doomed, kept = subprocess.Popen(sleeper), subprocess.Popen(sleeper)
    table = [(doomed.pid, 7200.0, f"{sys.executable} -m poolhouse.node_launch supervise --state {mine}/node"),
             (kept.pid, 7200.0, f"{sys.executable} -m poolhouse.node_launch supervise --state {other}/node")]
    try:
        said = []
        assert testreap.reap((str(mine),), say=said.append, listing=lambda: table) == 1
        assert doomed.wait(timeout=10) != 0 and kept.poll() is None
        assert said and "stopped 1" in said[0]
    finally:
        for child in (doomed, kept):
            child.kill()
            child.wait()
        mine.rmdir()
        other.rmdir()


def test_the_process_listing_reads_pid_age_and_the_whole_command_line(monkeypatch):
    out = "  101  06:23 /usr/bin/python3 -m poolhouse.node_launch supervise --state /tmp/mlabc/node\n  102 1-02:03:04 /bin/sleep 5\nbad line\n"
    monkeypatch.setattr(testreap.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    assert testreap.processes() == [(101, 383.0, "/usr/bin/python3 -m poolhouse.node_launch supervise --state /tmp/mlabc/node"),
                                    (102, 93784.0, "/bin/sleep 5")]


def test_a_listing_that_cannot_be_read_reaps_nothing():
    def refused():
        raise PermissionError("ps")

    assert testreap.reap(listing=refused) == 0


def test_a_command_that_only_mentions_a_supervisor_is_not_stale():
    mention = "/bin/zsh -c grep 'poolhouse.node_launch supervise --state /tmp/mlabc/node' log"
    assert testreap.stale([(5, 99999.0, mention)]) == []


def test_a_supervisor_stops_when_the_process_that_owns_its_node_has_exited():
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
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


def test_the_suite_marks_its_own_process_as_the_owner_of_every_node_it_starts():
    assert os.environ[node_supervise.PARENT_ENV] == str(os.getpid())

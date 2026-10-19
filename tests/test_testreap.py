"""A test node whose run is gone is stopped: by its supervisor watching the run's process, and by the runner at its start."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
import pytest
import testreap
from testreap import Proc

from poolhouse import node_launch, node_supervise
from poolhouse.lock import pid_alive

SUPERVISOR = "/usr/bin/python3 -m poolhouse.node_launch supervise --state {state} --arg=--presence --arg=terminal"
WATCHED = SUPERVISOR + " --parent-pid {owner}"
NODE = "/var/folders/x/T/pytest-of-someone/pytest-3/popen-gw1/node0/poolhouse-node run --state {state} --presence terminal"
NOW = 1_000_000.0
OLD = testreap.MIN_AGE_S + 1


def row(pid, parent, age, template, state, **given):
    return Proc(pid, parent, NOW - age, template.format(state=state, owner=given.get("owner", 0)))


def stale(rows, owners_gone=()):
    return testreap.stale(rows, now=NOW, gone=lambda pid, started: pid in owners_gone)


def exited_unreaped():
    """A child that has exited and has not been waited for."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.monotonic() + 20
    while psutil.Process(child.pid).status() != psutil.STATUS_ZOMBIE:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    return child


def sleeper(*words):
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", *words])


def test_a_supervisor_is_stale_when_the_process_it_watches_has_gone_and_not_before_however_old_it_is():
    table = [row(11, 1, OLD * 10, WATCHED, "/tmp/mlabc/node", owner=500),
             row(12, 1, 5.0, WATCHED, "/private/tmp/mlxyz/node", owner=501),
             row(13, 1, 5.0, WATCHED, "/tmp/mlnew/node", owner=502)]
    assert stale(table, owners_gone={501}) == [12]
    assert stale(table, owners_gone={500, 501, 502}) == [11, 12, 13]
    assert stale(table) == []


def test_only_old_supervisors_that_name_no_process_in_a_test_state_folder_are_stale_by_age():
    table = [row(12, 1, OLD, SUPERVISOR, "/private/tmp/mlxyz/node"), row(13, 1, 60.0, SUPERVISOR, "/tmp/mlnew/node"),
             row(14, 1, OLD, SUPERVISOR, "/Users/someone/.poolhouse/node"), row(15, 1, OLD, SUPERVISOR, "/tmp/other/node"),
             row(16, 1, OLD, "/usr/bin/python3 -m pytest /tmp/mlabc/node", "/tmp/mlabc/node")]
    assert stale(table) == [12]


def test_a_node_is_stale_with_its_stale_supervisor_or_when_orphaned_for_the_minimum_age_and_never_while_its_parent_runs():
    table = [row(20, 1, OLD, SUPERVISOR, "/tmp/mlold/node"), row(21, 20, 5.0, NODE, "/tmp/mlold/node"),
             row(30, 1, OLD * 5, WATCHED, "/tmp/mllive/node", owner=600), row(31, 30, OLD * 5, NODE, "/tmp/mllive/node"),
             row(41, 1, OLD, NODE, "/tmp/mlorphan/node"), row(42, 1, 60.0, NODE, "/tmp/mlyoung/node"),
             row(43, 700, OLD, NODE, "/tmp/mlgone/node"), row(44, 701, OLD, NODE, "/tmp/mlalive/node")]
    assert stale(table, owners_gone={700}) == [20, 21, 41, 43]


def test_the_supervisors_are_stopped_before_the_nodes():
    table = [row(11, 1, OLD, NODE, "/tmp/mlabc/node"), row(12, 1, OLD, SUPERVISOR, "/private/tmp/mlxyz/node")]
    assert stale(table) == [12, 11]


def test_a_command_with_no_state_folder_is_never_stale():
    assert stale([Proc(1, 1, 0.0, "/usr/bin/python3 -m poolhouse.node_launch supervise --parent-pid 5")], owners_gone={5}) == []


def test_a_command_that_only_mentions_a_supervisor_is_not_stale():
    mention = "/bin/zsh -c grep 'poolhouse.node_launch supervise --state /tmp/mlabc/node' log"
    assert stale([Proc(5, 1, 0.0, mention)]) == []


def test_only_python_runs_a_supervisor_and_only_the_node_binary_runs_a_node():
    other = [Proc(5, 1, 0.0, "/usr/bin/perl -m poolhouse.node_launch supervise --state /tmp/mlabc/node --parent-pid 9"),
             Proc(6, 1, 0.0, "/usr/bin/ruby -m poolhouse.node_launch supervise --state /tmp/mlabc/node"),
             Proc(7, 1, 0.0, "/opt/tools/other run --state /tmp/mlabc/node")]
    assert stale(other, owners_gone={9}) == []
    versioned = Proc(8, 1, 0.0, "/opt/homebrew/bin/python3.13 -m poolhouse.node_launch supervise --state /tmp/mlabc/node --parent-pid 9")
    assert stale([versioned], owners_gone={9}) == [8]


def test_the_process_listing_reads_pid_parent_start_and_the_whole_command_line():
    child = sleeper("--listed marker")
    try:
        found = next(r for r in testreap.processes() if r.pid == child.pid)
    finally:
        child.kill()
        child.wait()
    assert found.parent == os.getpid() and abs(found.started - time.time()) < 60
    assert found.command == " ".join([sys.executable, "-c", "import time; time.sleep(120)", "--listed marker"])


def test_a_listing_that_cannot_be_read_reaps_nothing():
    def refused():
        raise PermissionError("ps")

    assert testreap.reap(listing=refused) == 0


def test_an_owner_has_gone_when_it_exited_whether_or_not_it_was_waited_for_or_its_number_was_taken():
    owner = sleeper()
    try:
        started = psutil.Process(owner.pid).create_time()
        assert testreap.owner_gone(owner.pid, started) is False
        assert testreap.owner_gone(owner.pid, started - 3600) is True
        owner.kill()
        owner.wait()
        assert testreap.owner_gone(owner.pid, started) is True
    finally:
        owner.kill()
        owner.wait()
    zombie = exited_unreaped()
    try:
        assert testreap.owner_gone(zombie.pid, time.time()) is True
    finally:
        zombie.wait()


def test_a_process_that_exited_and_was_not_waited_for_is_not_alive():
    zombie = exited_unreaped()
    try:
        assert pid_alive(zombie.pid) is False
        assert node_supervise._until_parent_gone(lambda: False, zombie.pid)() is True
    finally:
        zombie.wait()
    assert pid_alive(os.getpid()) is True


def test_a_listed_process_is_unchanged_only_while_its_start_and_command_line_are_the_ones_listed():
    child = sleeper("--marker")
    try:
        listed = next(r for r in testreap.processes() if r.pid == child.pid)
        assert testreap.unchanged(listed) is True
        assert testreap.unchanged(listed._replace(started=listed.started - 600)) is False
        assert testreap.unchanged(listed._replace(command=listed.command + " --other")) is False
    finally:
        child.kill()
        child.wait()
    assert testreap.unchanged(listed) is False


def test_a_pid_that_now_belongs_to_another_process_is_not_stopped():
    mine = Path(tempfile.mkdtemp(prefix="mlreap", dir="/tmp"))
    bystander = sleeper()
    stale_row = Proc(bystander.pid, 1, time.time() - OLD * 10, f"{sys.executable} -m poolhouse.node_launch supervise --state {mine}/node")
    try:
        assert testreap.reap((str(mine),), listing=lambda: [stale_row]) == 0
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()
        mine.rmdir()


def supervisor_of(state: Path, owner: int, fake: Path) -> subprocess.Popen:
    """A process whose command line is that of a test supervisor watching ``owner``, running a stand-in module that sleeps."""
    (fake / "poolhouse").mkdir(parents=True, exist_ok=True)
    (fake / "poolhouse" / "__init__.py").write_text("")
    (fake / "poolhouse" / "node_launch.py").write_text("import time\ntime.sleep(120)\n")
    return subprocess.Popen([sys.executable, "-m", "poolhouse.node_launch", "supervise", "--state", f"{state}/node",
                             "--parent-pid", str(owner)], env={**os.environ, "PYTHONPATH": str(fake)})


def test_a_supervisor_whose_owner_has_exited_is_stopped_and_one_whose_owner_runs_is_not(tmp_path):
    mine = Path(tempfile.mkdtemp(prefix="mlreap", dir="/tmp"))
    owner = sleeper()
    doomed = supervisor_of(mine, owner.pid, tmp_path / "a")
    kept = supervisor_of(mine, os.getpid(), tmp_path / "b")
    try:
        deadline = time.monotonic() + 20
        while len([r for r in testreap.processes() if r.pid in (doomed.pid, kept.pid) and "--parent-pid" in r.command]) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.05)
        owner.kill()
        owner.wait()
        said = []
        assert testreap.reap((str(mine),), say=said.append) == 1
        assert doomed.wait(timeout=10) != 0 and kept.poll() is None
        assert said and "stopped 1" in said[0]
    finally:
        for child in (doomed, kept, owner):
            child.kill()
            child.wait()
        mine.rmdir()


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


def test_the_suite_marks_its_own_process_as_the_owner_of_every_node_it_starts():
    assert os.environ[node_supervise.PARENT_ENV] == str(os.getpid())


class Unreadable:
    """A process whose command line cannot be read, as `psutil` reports one on macOS (a permission error, a system error)."""

    def __init__(self, pid, error):
        self.pid, self.error = pid, error

    def cmdline(self):
        raise self.error


def iterate(readable, unreadable):
    """A stand-in for `psutil.process_iter` that fails the way macOS does when it is asked for attributes up front."""
    def process_iter(attrs=None, ad_value=None):
        if attrs:
            raise PermissionError("sysctl KERN_PROCARGS2")
        yield from unreadable
        yield from readable
    return process_iter


def test_a_process_whose_command_line_cannot_be_read_is_left_out_of_the_listing(monkeypatch):
    errors = [PermissionError("sysctl"), SystemError("error return without exception set"), psutil.AccessDenied(7), psutil.ZombieProcess(8)]
    monkeypatch.setattr(testreap.psutil, "process_iter", iterate([psutil.Process()], [Unreadable(7 + i, e) for i, e in enumerate(errors)]))
    pids = [found.pid for found in testreap.processes()]
    assert pids == [os.getpid()]


@pytest.mark.parametrize("error", [PermissionError("sysctl"), SystemError("error return without exception set"), psutil.AccessDenied(7)])
def test_a_listing_that_fails_with_a_system_error_reaps_nothing(error):
    def refused():
        raise error

    assert testreap.reap(listing=refused) == 0

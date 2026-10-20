"""A daemon a test started is stopped when the test ends, and by the runner when its run is gone."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import daemon_guard
import psutil
import testreap
from testreap import Proc

from poolhouse import jobs, node_supervise

DAEMON = "/usr/bin/python3 -m poolhouse.cli.daemon --port 6100 --root {root} --name studio --mode prod"
TMP = "/private/var/folders/x/T/pytest-of-someone"
ROOT = re.compile(r"--root (\S+)")
NOW = 1_000_000.0
OLD = testreap.MIN_AGE_S + 1


def daemon_row(pid, root, *, parent=1, age=5.0, template=DAEMON):
    return Proc(pid, parent, NOW - age, template.format(root=root))


def stale(rows, *, owners=None, recorded=None, gone=(), roots=(TMP,)):
    """The stale daemons of ``rows``; ``owners`` maps a pid to the process its environment names, ``recorded`` a pid to the pid its run record holds."""
    roots_of = {row.pid: found.group(1) for row in rows if (found := ROOT.search(row.command))}
    held = {root: (pid if recorded is None else recorded.get(pid)) for pid, root in roots_of.items()}
    readers = testreap.Readers(gone=lambda pid, started: pid in gone, owner_of=lambda pid: (owners or {}).get(pid), recorded=held.get)
    return testreap.stale_daemons(rows, roots, now=NOW, readers=readers)


def test_a_daemon_is_stale_once_the_process_its_environment_names_has_gone_however_young_it_is():
    table = [daemon_row(11, f"{TMP}/pytest-3/popen-gw1/t0/traind"), daemon_row(12, f"{TMP}/pytest-3/popen-gw2/t0/traind")]
    assert stale(table, owners={11: 500, 12: 501}, gone={500}) == [11]
    assert stale(table, owners={11: 500, 12: 501}) == []


def test_a_daemon_that_names_no_owner_is_stale_when_old_and_orphaned_and_not_before():
    table = [daemon_row(11, f"{TMP}/pytest-3/a/traind", age=OLD), daemon_row(12, f"{TMP}/pytest-3/b/traind", age=60.0),
             daemon_row(13, f"{TMP}/pytest-3/c/traind", age=OLD, parent=700), daemon_row(14, f"{TMP}/pytest-3/d/traind", age=OLD, parent=701)]
    assert stale(table, gone={700}) == [11, 13]


def test_only_a_daemon_with_a_root_under_the_test_folder_is_ever_stale():
    table = [daemon_row(11, "/Users/someone/.poolhouse/traind", age=OLD), daemon_row(12, "/private/var/folders/x/T/elsewhere/traind", age=OLD),
             daemon_row(13, f"{TMP}/pytest-3/a/traind", age=OLD)]
    assert stale(table, owners={11: 500, 12: 500, 13: 500}, gone={500}) == [13]


def test_a_daemon_is_not_stale_when_the_run_record_under_its_root_names_another_process():
    table = [daemon_row(11, f"{TMP}/pytest-3/a/traind", age=OLD), daemon_row(12, f"{TMP}/pytest-3/b/traind", age=OLD),
             daemon_row(13, f"{TMP}/pytest-3/c/traind", age=OLD)]
    assert stale(table, owners={11: 500, 12: 500, 13: 500}, gone={500}, recorded={11: 11, 12: 99}) == [11, 13]


def test_only_python_running_the_daemon_module_is_a_daemon():
    other = [Proc(5, 1, NOW - OLD, f"/usr/bin/perl -m poolhouse.cli.daemon --root {TMP}/pytest-3/a/traind"),
             Proc(6, 1, NOW - OLD, f"/bin/zsh -c grep 'poolhouse.cli.daemon --root {TMP}/pytest-3/a/traind' log"),
             Proc(7, 1, NOW - OLD, f"/usr/bin/python3 -m poolhouse.cli.daemon_other --root {TMP}/pytest-3/a/traind")]
    assert stale(other, owners={5: 500, 6: 500, 7: 500}, gone={500}) == []


def test_the_recorded_pid_is_read_from_the_file_start_daemon_writes(tmp_path):
    assert testreap.recorded_pid(str(tmp_path)) is None
    from poolhouse.fleet.join import STARTED_FILE

    (tmp_path / STARTED_FILE).write_text(json.dumps({"pid": 4242}))
    assert testreap.recorded_pid(str(tmp_path)) == 4242
    (tmp_path / STARTED_FILE).write_text("not json")
    assert testreap.recorded_pid(str(tmp_path)) is None


def stand_in_daemon(root: Path, owner: int, fake: Path) -> subprocess.Popen:
    """A process with the command line of a daemon whose root holds its run record, running a module that sleeps."""
    from poolhouse.fleet.join import STARTED_FILE

    (fake / "poolhouse" / "cli").mkdir(parents=True, exist_ok=True)
    (fake / "poolhouse" / "__init__.py").write_text("")
    (fake / "poolhouse" / "cli" / "__init__.py").write_text("")
    (fake / "poolhouse" / "cli" / "daemon.py").write_text("import time\ntime.sleep(120)\n")
    root.mkdir(parents=True, exist_ok=True)
    child = subprocess.Popen([sys.executable, "-m", "poolhouse.cli.daemon", "--port", "6100", "--root", str(root)],
                             env={**os.environ, "PYTHONPATH": str(fake), node_supervise.PARENT_ENV: str(owner)})
    (root / STARTED_FILE).write_text(json.dumps({"pid": child.pid}))
    return child


def test_a_daemon_whose_run_is_gone_is_stopped_and_one_whose_run_is_alive_is_left_alone(tmp_path):
    mine = Path(tempfile.mkdtemp(prefix="pytest-of-reap", dir=tempfile.gettempdir())).resolve()
    owner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    doomed = stand_in_daemon(mine / "a" / "traind", owner.pid, tmp_path / "a")
    kept = stand_in_daemon(mine / "b" / "traind", os.getpid(), tmp_path / "b")
    try:
        deadline = time.monotonic() + 20
        while not all(testreap.daemon_environment(p.pid) for p in (doomed, kept)):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        owner.kill()
        owner.wait()
        said = []
        assert testreap.reap_daemons((str(mine),), say=said.append) == 1
        assert doomed.wait(timeout=10) != 0 and kept.poll() is None
        assert said and "stopped 1" in said[0]
    finally:
        for child in (doomed, kept, owner):
            child.kill()
            child.wait()


def test_a_daemon_outside_the_given_roots_is_not_stopped_even_when_its_run_is_gone(tmp_path):
    mine = Path(tempfile.mkdtemp(prefix="pytest-of-reap", dir=tempfile.gettempdir())).resolve()
    owner = subprocess.Popen([sys.executable, "-c", "pass"])
    owner.wait()
    bystander = stand_in_daemon(mine / "a" / "traind", owner.pid, tmp_path)
    try:
        deadline = time.monotonic() + 20
        while not testreap.daemon_environment(bystander.pid):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert testreap.reap_daemons((str(mine / "elsewhere"),)) == 0
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait()


def test_a_daemon_a_test_starts_is_stopped_with_its_children_when_the_test_ends(tmp_path, monkeypatch):
    fake = tmp_path / "fake"
    (fake / "poolhouse" / "cli").mkdir(parents=True)
    (fake / "poolhouse" / "__init__.py").write_text("")
    (fake / "poolhouse" / "cli" / "__init__.py").write_text("")
    (fake / "poolhouse" / "cli" / "daemon.py").write_text(
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "pathlib.Path(sys.argv[-1]).write_text(str(child.pid))\n"
        "time.sleep(120)\n")
    monkeypatch.setenv("PYTHONPATH", str(fake))
    monkeypatch.setattr(jobs.runtime, "available", lambda: None)
    monkeypatch.setattr(jobs.runtime, "python", lambda: sys.executable)
    grandchild = tmp_path / "grandchild.pid"
    with daemon_guard.watching() as started:
        ran = jobs.detach("poolhouse.cli.daemon", [str(grandchild)], log=tmp_path / "daemon.log")
        deadline = time.monotonic() + 20
        while not grandchild.is_file() or not grandchild.read_text():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        child = int(grandchild.read_text())
        assert psutil.pid_exists(ran.pid) and psutil.pid_exists(child)
        assert started == [ran.pid]
    for pid in (ran.pid, child):
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE


def test_a_process_that_is_not_the_daemon_module_is_not_tracked(tmp_path):
    with daemon_guard.watching() as started:
        jobs.detach("platform", [], log=tmp_path / "other.log")
        assert started == []

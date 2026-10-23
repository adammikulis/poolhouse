"""A daemon started from a terminal, an editor or an agent's shell answers for itself when macOS asks who is responsible for it.

macOS attributes a prompt to the responsible process, which a child inherits from whatever started it. These tests ask the kernel
(`responsibility_get_pid_responsible_for_pid`) about real processes: one started the ordinary way, one with its responsibility
disclaimed.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from poolhouse import disclaim, jobs

SLEEP = ["-c", "import time; time.sleep(30)"]


@pytest.fixture
def macos():
    """The test needs macOS: responsibility is a macOS notion."""
    if sys.platform != "darwin":
        pytest.skip("responsibility is a macOS notion")


def responsible(pid: int) -> int:
    ask = ctypes.CDLL("/usr/lib/libSystem.B.dylib").responsibility_get_pid_responsible_for_pid
    ask.argtypes, ask.restype = [ctypes.c_int], ctypes.c_int
    return ask(pid)


def stop(pid: int) -> None:
    try:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
    except (ProcessLookupError, ChildProcessError):
        pass


@pytest.mark.usefixtures("macos")
def test_a_child_started_the_ordinary_way_is_the_responsibility_of_whatever_ran_it(tmp_path):
    child = subprocess.Popen([sys.executable, *SLEEP], start_new_session=True)
    try:
        time.sleep(0.3)
        assert responsible(child.pid) != child.pid, "the prompt for this one would name the app that started it"
    finally:
        child.kill()
        child.wait()


@pytest.mark.usefixtures("macos")
def test_a_disclaimed_child_is_its_own_responsible_process_and_keeps_its_output(tmp_path):
    out = tmp_path / "out.log"
    with out.open("ab") as stream:
        pid = disclaim.spawn([sys.executable, "-c", "import sys,time; print('hello', flush=True); print('oops', file=sys.stderr, flush=True); time.sleep(30)"],
                             {"PATH": os.environ["PATH"]}, stream.fileno())
    try:
        time.sleep(1.0)
        assert responsible(pid) == pid
        assert os.getsid(pid) == pid, "it leads a session of its own, as a detached process does"
        assert out.read_text().splitlines() == ["hello", "oops"]
    finally:
        stop(pid)


@pytest.mark.usefixtures("macos")
def test_a_command_that_cannot_start_is_said_not_ignored(tmp_path):
    with (tmp_path / "out.log").open("ab") as stream, pytest.raises(disclaim.DisclaimError, match="starting /nowhere/bin/python"):
        disclaim.spawn(["/nowhere/bin/python"], {}, stream.fileno())


@pytest.mark.usefixtures("macos")
def test_the_host_restart_starts_the_daemon_as_its_own_responsible_process(tmp_path):
    ran = jobs.detach("timeit", ["-n", "1", "-r", "1", "import time; time.sleep(30)"], log=tmp_path / "restart.log", disclaimed=True)
    try:
        time.sleep(1.0)
        assert ran.process is None and responsible(ran.pid) == ran.pid
        assert (tmp_path / "restart.log").read_text().startswith("argv: ")
    finally:
        stop(ran.pid)


def test_where_nothing_can_be_disclaimed_the_ordinary_start_is_used(tmp_path, monkeypatch):
    monkeypatch.setattr(disclaim, "available", lambda: False)
    monkeypatch.setattr(disclaim, "spawn", lambda *a, **k: pytest.fail("disclaiming was attempted where it does not exist"))
    ran = jobs.detach("timeit", ["-n", "1", "-r", "1", "pass"], log=tmp_path / "x.log", disclaimed=True)
    assert ran.process is not None
    ran.process.wait(timeout=60)
    assert Path(tmp_path / "x.log").exists()


@pytest.mark.usefixtures("macos")
def test_a_descriptor_the_caller_holds_does_not_reach_the_daemon(tmp_path):
    held = os.open(tmp_path, os.O_RDONLY)
    os.set_inheritable(held, True)
    try:
        with (tmp_path / "out.log").open("ab") as stream:
            pid = disclaim.spawn([sys.executable, "-c", "import os; print(sorted(int(n) for n in os.listdir('/dev/fd')), flush=True)"],
                                 {"PATH": os.environ["PATH"]}, stream.fileno())
        time.sleep(1.5)
        stop(pid)
        seen = (tmp_path / "out.log").read_text()
        assert str(held) not in seen.replace("[", " ").replace("]", " ").replace(",", " ").split(), seen
    finally:
        os.close(held)


@pytest.mark.usefixtures("macos")
def test_a_name_that_is_not_utf8_is_carried_as_the_system_has_it(tmp_path):
    with (tmp_path / "out.log").open("ab") as stream:
        pid = disclaim.spawn([sys.executable, "-c", "import os; print(os.fsencode(os.environ['ODD']), flush=True)"],
                             {"PATH": os.environ["PATH"], "ODD": os.fsdecode(b"caf\xe9")}, stream.fileno())
    time.sleep(1.5)
    stop(pid)
    assert "caf" in (tmp_path / "out.log").read_text()


def _printed(path: Path) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and not path.read_text().strip():
        time.sleep(0.05)
    return path.read_text().strip()


@pytest.mark.usefixtures("macos")
def test_a_disclaimed_child_starts_in_the_directory_it_is_given_not_the_callers(tmp_path):
    where = tmp_path / "elsewhere"
    where.mkdir()
    with (tmp_path / "out.log").open("ab") as stream:
        pid = disclaim.spawn([sys.executable, "-I", "-S", "-c", "import os; print(os.getcwd(), flush=True)"],
                             {"PATH": os.environ["PATH"]}, stream.fileno(), cwd=where)
    try:
        assert Path(_printed(tmp_path / "out.log")).resolve() == where.resolve()
    finally:
        stop(pid)


@pytest.mark.usefixtures("macos")
def test_a_job_started_as_its_own_responsible_process_starts_in_the_state_root_not_where_it_was_run_from(tmp_path, monkeypatch):
    """macOS makes a process that answers for itself ask for its own access to Documents, Desktop and Downloads, and waits for an
    answer nobody sees; so the directory the caller happens to be in (often a checkout there) is never the one it starts in."""
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    with (tmp_path / "out.log").open("ab") as stream:
        ran = jobs.start([sys.executable, "-I", "-S", "-c", "import os; print(os.getcwd(), flush=True)"], out=stream, disclaimed=True)
    try:
        assert Path(_printed(tmp_path / "out.log")).resolve() == (tmp_path / "state").resolve()
    finally:
        stop(ran.pid)

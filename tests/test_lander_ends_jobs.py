"""A run that stops takes the test jobs it started with it: a foreground test run and a lander, real processes, signals and job table."""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import testjobs  # noqa: E402
import testreuse_store as storage  # noqa: E402

pytestmark = pytest.mark.slow

BOUND_S = 40.0

RUNNER = f'''
import os, signal, sys, time
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
import testjobs
import testreuse_store as storage

jobs = testjobs.Jobs(Path(os.environ["JOBS_HOME"]))
job = sys.argv[2]
me = {{"pid": os.getpid(), "started": storage.process_start(os.getpid())}}
jobs.write(job, "status.json", {{"state": "running", "at": time.time(), **me}})
signal.signal(signal.SIGTERM, lambda *_: (jobs.write(job, "status.json", {{"state": "cancelled", "exit": 130,
    "finished": time.time(), **me}}), os._exit(130)))
jobs.watch_follower(job)
time.sleep(600)
'''

FOLLOW = f'''
import os, sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
import testjobs

jobs = testjobs.Jobs(Path(os.environ["JOBS_HOME"]))
sys.exit(jobs.follow(["tests/x.py"], {{"id": "alice"}}, Path(os.environ["JOBS_ROOT"]), Path(os.environ["JOBS_RUNNER"]), {{}}))
'''

LANDER = f'''
import os, sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPTS)!r})
import land_exec
import land_steps

land_steps.stop_on_signals()
land_exec.run_check(Path(os.environ["JOBS_ROOT"]), land_exec.Check("tests", [sys.executable, os.environ["JOBS_FOLLOW"]], env=dict(os.environ)))
'''


@pytest.fixture
def table(tmp_path, monkeypatch):
    (tmp_path / "tests").mkdir()
    for name, text in (("runner.py", RUNNER), ("follow.py", FOLLOW), ("lander.py", LANDER)):
        (tmp_path / name).write_text(text)
    env = {"JOBS_HOME": str(tmp_path / "store"), "JOBS_ROOT": str(tmp_path), "JOBS_RUNNER": str(tmp_path / "runner.py"),
           "JOBS_FOLLOW": str(tmp_path / "follow.py")}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return testjobs.Jobs(tmp_path / "store"), tmp_path


def start(script: Path) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)


def running(jobs: testjobs.Jobs) -> tuple[str, int, float]:
    """The job whose runner is up, with its pid and start time."""
    deadline = time.monotonic() + BOUND_S
    while time.monotonic() < deadline:
        for job in jobs.ids():
            status = jobs.read(job)
            if status.get("state") == "running" and status.get("pid"):
                time.sleep(0.3)
                return job, int(status["pid"]), float(status["started"])
        time.sleep(0.1)
    raise AssertionError("the job's runner never came up")


def gone(pid: int, started: float) -> bool:
    deadline = time.monotonic() + BOUND_S
    while time.monotonic() < deadline:
        if not storage.alive(pid, started):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
def test_a_foreground_run_that_is_signalled_cancels_its_job(table, number):
    jobs, tmp = table
    run = start(tmp / "follow.py")
    job, pid, started = running(jobs)
    run.send_signal(number)
    out, _ = run.communicate(timeout=BOUND_S)
    assert run.returncode == 130, out
    assert gone(pid, started), "the job's runner outlived the run that followed it"
    assert jobs.state(job)["state"] == "cancelled" and f"job {job} cancelled" in out


def test_a_foreground_run_that_is_killed_leaves_no_job_behind(table):
    jobs, tmp = table
    run = start(tmp / "follow.py")
    job, pid, started = running(jobs)
    run.kill()
    run.communicate(timeout=BOUND_S)
    assert gone(pid, started), "the job's runner outlived the run that followed it"
    assert "the run that was following it ended" in (jobs.path(job) / "log").read_text()


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGHUP])
def test_a_lander_that_is_signalled_cancels_the_test_job_of_its_check(table, number):
    jobs, tmp = table
    lander = start(tmp / "lander.py")
    _, pid, started = running(jobs)
    lander.send_signal(number)
    out, _ = lander.communicate(timeout=BOUND_S + 30)
    assert lander.returncode == 143, out
    assert gone(pid, started), "the job's runner outlived the lander"


def test_a_lander_that_hits_an_error_cancels_the_test_job_of_its_check(table):
    jobs, tmp = table
    (tmp / "lander.py").write_text(LANDER.replace("land_steps.stop_on_signals()", "import threading, _thread\n"
                                                  "threading.Timer(3, _thread.interrupt_main).start()"))
    lander = start(tmp / "lander.py")
    _, pid, started = running(jobs)
    out, _ = lander.communicate(timeout=BOUND_S + 30)
    assert "KeyboardInterrupt" in out
    assert gone(pid, started), "the job's runner outlived the lander"


def test_cancelling_a_job_whose_runner_never_ends_is_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(testjobs, "ENDS_S", 0.2)
    monkeypatch.setattr(testjobs, "KILLED_S", 0.2)
    monkeypatch.setattr(storage, "alive", lambda pid, started: True)
    with pytest.raises(OSError, match="still running after it was cancelled"):
        testjobs.Jobs(tmp_path).reap("job", 2**22 + 12345, 1.0)

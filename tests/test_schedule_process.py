"""The real processes of a scheduled job: a real sandbox is refused, and a job's own detached process waits, is cancelled,
reports a missed time and is started again after it was killed.

These run outside the suite's real-state write denial (tests/seatbelt-modules.txt): that denial is itself a sandbox, and a job's
process refuses to run in one. Nothing is measured, no model server is asked for and no model runs.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from poolhouse import caller, runtime, schedule_record as record
from poolhouse.schedule import cli, spawn, who

REPO = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.slow
ASK = "from poolhouse.schedule import who; print(who.refused_here() or 'free')"


def python(*words: str, sandbox: str = "", timeout: float = 120) -> str:
    command = [sys.executable, "-B", *words]
    if sandbox:
        command = ["/usr/bin/sandbox-exec", "-p", sandbox, *command]
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False,
                          env={**os.environ, "PYTHONPATH": str(REPO / "src")})
    return done.stdout + done.stderr


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS's sandbox")
@pytest.mark.parametrize("profile", ["(version 1)(allow default)", "(version 1)(allow default)(deny network*)"])
def test_a_process_in_a_real_sandbox_is_refused_and_one_outside_is_not(profile):
    assert "runs in a sandbox" in python("-c", ASK, sandbox=profile)
    assert python("-c", ASK).strip() == "free"


@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt is macOS's sandbox")
def test_the_command_in_a_real_sandbox_adds_nothing(tmp_path):
    file = tmp_path / "night.queue"
    file.write_text("delegate --model m --task x\n", encoding="utf-8")
    # pytest is imported first so the child counts as a test run: its refusal is audited in this test's state, not the account's
    words = ["schedule", "add", str(file), "--at", "+30m"]
    said = python("-c", f"import pytest, sys; from poolhouse.cli import main; sys.exit(main({words!r}))",
                  sandbox="(version 1)(allow default)")
    assert "runs in a sandbox" in said and "scheduled s-" not in said, said
    assert record.jobs() == []
    assert [row["action"] for row in caller.audit_rows() if row["event"] == "refused"] == ["scheduling a job"]


@pytest.fixture
def started(monkeypatch):
    """Jobs whose processes this test starts from this checkout; every one left is ended."""
    monkeypatch.setattr(runtime, "available", lambda: None)
    jobs: list[str] = []
    yield jobs
    for job in jobs:
        for process in processes(job):
            process.kill()


def processes(job: str) -> list:
    out = []
    for process in psutil.process_iter(["cmdline"]):
        words = process.info.get("cmdline") or []
        if "poolhouse.schedule.cli" in words and "hold" in words and job in words:
            out.append(process)
    return out


def until(predicate, seconds: float = 60.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            pytest.fail("the job's process did not settle")
        time.sleep(0.2)


def stored(at: float, latest: float) -> str:
    return record.create({"name": "night", "at": at, "latest": latest, "max_hours": 1.0, "created": time.time(), "steps": [],
                          "by": {"who": "user tester", "board": "", "agent": "", "cwd": "/"}, "code": {}, "pins": {},
                          "child": None})["id"]


def test_a_jobs_own_process_holds_it_outlives_no_parent_and_ends_it_when_cancelled(started):
    job = stored(time.time() + 3600, time.time() + 7200)
    started.append(job)
    assert who.refused_here() == ""
    assert spawn.ensure() == [job]
    until(lambda: record.alive(job) and record.load(job)["checked"] > 0)
    [process] = processes(job)
    assert os.getsid(process.pid) != os.getsid(0), "it belongs to no terminal"
    environment = process.environ()
    assert not [name for name in caller.AGENT_MARKERS if name in environment]
    record.cancel(job, "user tester")
    until(lambda: record.load(job)["state"] == "cancelled" and not record.alive(job))


def test_adding_a_job_returns_only_once_its_own_process_holds_it(started, tmp_path):
    file = tmp_path / "night.queue"
    file.write_text("delegate --model m --task x\n", encoding="utf-8")
    now = time.time()
    job = cli.add_job(file, cli.Asked(now + 3600, now + 7200), identity={"who": "user tester", "sessions": []},
                      cwd=tmp_path)["id"]
    started.append(job)
    assert record.alive(job), "a job reported as scheduled has a process holding it"
    holder = record.view(record.load(job))["holder"]
    [process] = processes(job)
    assert holder["pid"] == process.pid and holder["python"]
    record.cancel(job, "user tester")
    until(lambda: record.load(job)["state"] == "cancelled" and not record.alive(job))


def test_a_killed_waiting_process_is_started_again_and_a_passed_time_is_reported_missed(started):
    job = stored(time.time() + 4, time.time() + 8)
    started.append(job)
    assert spawn.ensure() == [job]
    until(lambda: record.alive(job))
    for process in processes(job):
        process.send_signal(signal.SIGKILL)
    until(lambda: not record.alive(job))
    assert record.load(job)["state"] == "waiting"
    time.sleep(max(0.0, record.load(job)["latest"] - time.time()) + 1)
    assert spawn.ensure(now=time.time() + spawn.AGAIN_S) == [job]
    until(lambda: record.load(job)["state"] == "missed" and not record.alive(job))
    kept = record.load(job)
    assert kept["why"] and not kept["notice"]["sent"] and "no board" in kept["notice"]["error"]
    assert [row["state"] for row in caller.audit_rows() if row["event"] == "schedule.end"] == ["missed"]

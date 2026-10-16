"""The queue doctor ends background test jobs that have gone quiet or run too long, and leaves live ones alone."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import queue_doctor
import testjobs
import testreuse_store as storage


def job(folder: Path, name: str, *, created: float, log_age: float, process: subprocess.Popen | None) -> str:
    where = folder / "jobs" / name
    where.mkdir(parents=True)
    pid = process.pid if process else 0
    status = {"state": "running", "pid": pid, "started": storage.process_start(pid) if pid else 0.0, "at": created}
    (where / "status.json").write_text(json.dumps(status))
    (where / "spec.json").write_text(json.dumps({"id": name, "created": created}))
    log = where / "log"
    log.write_text("x")
    os.utime(log, (time.time() - log_age,) * 2)
    return name


def sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)


def test_a_job_with_a_quiet_log_is_ended_and_recorded_as_failed(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        name = job(tmp_path, "j1", created=time.time() - 3000, log_age=queue_doctor.QUIET_S + 60, process=process)
        said = []
        assert queue_doctor.sweep(jobs, say=said.append, sleep=lambda s: process.wait(timeout=10)) == [name]
        assert process.poll() is not None
        status = jobs.read(name)
        assert status["state"] == "failed" and "queue doctor" in status["detail"] and said
    finally:
        process.kill()


def test_a_job_that_is_producing_output_is_left_alone(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        name = job(tmp_path, "j2", created=time.time() - 3000, log_age=5, process=process)
        assert queue_doctor.sweep(jobs, sleep=lambda s: None) == []
        assert process.poll() is None and jobs.read(name)["state"] == "running"
    finally:
        process.kill()


def test_a_job_older_than_the_limit_is_ended_even_with_fresh_output(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        job(tmp_path, "j3", created=time.time() - queue_doctor.MAX_AGE_S - 60, log_age=1, process=process)
        assert queue_doctor.sweep(jobs, sleep=lambda s: process.wait(timeout=10)) == ["j3"]
    finally:
        process.kill()


def test_a_reused_pid_is_never_signalled(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        name = job(tmp_path, "j4", created=time.time() - 3000, log_age=queue_doctor.QUIET_S + 60, process=process)
        status = jobs.read(name)
        jobs.write(name, "status.json", {**status, "started": status["started"] - 500})
        queue_doctor.sweep(jobs, sleep=lambda s: None)
        assert process.poll() is None
    finally:
        process.kill()


def test_a_finished_job_is_not_touched(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    name = job(tmp_path, "j5", created=time.time() - 99999, log_age=99999, process=None)
    jobs.write(name, "status.json", {"state": "done", "exit": 0})
    assert queue_doctor.sweep(jobs, sleep=lambda s: None) == []

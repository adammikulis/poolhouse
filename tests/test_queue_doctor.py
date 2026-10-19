"""The queue doctor ends background test jobs that have gone quiet or run too long, and leaves live ones alone."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import psutil
import queue_doctor
import testjobs
import testreuse_store as storage


def job(folder: Path, name: str, *, created: float, log_age: float, process: subprocess.Popen | None) -> str:
    where = folder / "jobs" / name
    where.mkdir(parents=True)
    pid = process.pid if process else 0
    status = {"state": "running", "pid": pid, "started": storage.process_start(pid) if pid else 0.0, "at": created}
    (where / "status.json").write_text(json.dumps(status))
    (where / "spec.json").write_text(json.dumps({"id": name, "created": created, "owner": {"id": "alice"}}))
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


def test_a_job_with_no_owner_is_ended_on_sight_even_while_it_writes_output(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        name = job(tmp_path, "j0", created=time.time(), log_age=1, process=process)
        (tmp_path / "jobs" / name / "spec.json").write_text(json.dumps({"id": name, "created": time.time(), "owner": {}}))
        said = []
        assert queue_doctor.sweep(jobs, say=said.append, sleep=lambda s: process.wait(timeout=10)) == [name]
        assert process.poll() is not None
        assert jobs.read(name)["state"] == "failed" and "it has no owner" in jobs.read(name)["detail"]
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


def test_a_job_older_than_the_limit_is_ended_once_it_has_gone_a_little_quiet(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        job(tmp_path, "j3", created=time.time() - queue_doctor.MAX_AGE_S - 60, log_age=queue_doctor.OLD_QUIET_S + 60, process=process)
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


def test_a_stray_entry_in_the_jobs_folder_does_not_stop_the_pass(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    (tmp_path / "jobs" / "stray.dir").mkdir(parents=True)
    process = sleeper()
    try:
        name = job(tmp_path, "j6", created=time.time() - 3000, log_age=queue_doctor.QUIET_S + 60, process=process)
        said = []
        assert queue_doctor.sweep(jobs, say=said.append, sleep=lambda s: process.wait(timeout=10)) == [name]
        assert any("stray.dir" in line for line in said)
    finally:
        process.kill()


def test_an_old_job_that_is_still_printing_is_left_alone(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        job(tmp_path, "j7", created=time.time() - queue_doctor.MAX_AGE_S - 600, log_age=5, process=process)
        assert queue_doctor.sweep(jobs, sleep=lambda s: None) == []
        assert process.poll() is None
    finally:
        process.kill()


def test_the_pass_survives_a_broken_jobs_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(queue_doctor, "jobs_of", lambda root: (_ for _ in ()).throw(OSError("no store")))
    said = []
    assert queue_doctor.mend(tmp_path, said.append) == [] and said


def test_a_test_node_older_than_its_limit_is_ended_and_a_young_one_is_not(tmp_path):
    program = tmp_path / "pytest-of-someone" / "run" / "poolhouse-node"
    program.parent.mkdir(parents=True)
    program.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(600)\n")
    program.chmod(0o755)
    process = subprocess.Popen([str(program), "run", "--state", str(tmp_path / "state")], start_new_session=True)
    try:
        time.sleep(0.5)
        mine = frozenset({process.pid})
        assert queue_doctor.reap_nodes(say=lambda text: None, only=mine) == []
        assert queue_doctor.reap_nodes(now=time.time() + queue_doctor.NODE_MAX_AGE_S + 60, say=lambda text: None, only=mine) == [process.pid]
        assert process.wait(timeout=10) is not None
    finally:
        process.kill()


def node_program(tmp_path: Path) -> Path:
    program = tmp_path / "pytest-of-someone" / "run" / "poolhouse-node"
    program.parent.mkdir(parents=True)
    program.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(600)\n")
    program.chmod(0o755)
    return program


def only_these(monkeypatch, pids: set[int]) -> None:
    original = psutil.process_iter
    monkeypatch.setattr(queue_doctor.psutil, "process_iter", lambda attrs=None: (p for p in original(attrs) if p.pid in pids))


def test_only_a_node_whose_pytest_run_is_gone_is_ended_by_the_doctor(monkeypatch, tmp_path):
    program = node_program(tmp_path)
    argv = [str(program), "run", "--state", str(tmp_path / "state")]
    live = subprocess.Popen(argv, start_new_session=True)
    spawner = "import subprocess, sys; D = subprocess.DEVNULL; print(subprocess.Popen(sys.argv[1:], start_new_session=True, stdin=D, stdout=D, stderr=D).pid)"
    orphan = int(subprocess.run([sys.executable, "-c", spawner, *argv], capture_output=True, text=True, check=True).stdout)
    try:
        for _ in range(100):
            if psutil.Process(orphan).ppid() <= 1 or psutil.Process(orphan).parent().name() in queue_doctor.INIT_NAMES:
                break
            time.sleep(0.05)
        only_these(monkeypatch, {live.pid, orphan})
        assert queue_doctor.orphaned(psutil.Process(orphan)) and not queue_doctor.orphaned(psutil.Process(live.pid))
        old = time.time() + queue_doctor.NODE_MAX_AGE_S + 60
        assert queue_doctor.reap_nodes(now=old, say=lambda text: None) == [orphan]
        assert live.poll() is None
    finally:
        live.kill()
        if psutil.pid_exists(orphan):
            psutil.Process(orphan).kill()


def test_the_pass_spares_an_old_node_whose_run_is_alive(monkeypatch, tmp_path):
    program = node_program(tmp_path)
    live = subprocess.Popen([str(program), "run", "--state", str(tmp_path / "state")], start_new_session=True)
    try:
        time.sleep(0.5)
        only_these(monkeypatch, {live.pid})
        monkeypatch.setattr(queue_doctor, "jobs_of", lambda root: testjobs.Jobs(tmp_path / "jobs-root"))
        old = time.time() + queue_doctor.NODE_MAX_AGE_S + 60
        assert queue_doctor.mend(tmp_path, say=lambda text: None, now=old) == []
        assert live.poll() is None
    finally:
        live.kill()

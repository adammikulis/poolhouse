"""The queue doctor ends a background test job whose runner is gone or that has no owner, tells the owner of a quiet or old one, and leaves live ones running."""

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
import queue_notice
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


def test_a_job_with_a_quiet_log_keeps_running_and_its_owner_is_told_once(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    told = []

    def tell(table, name, kind, text, say):
        told.append((name, kind, text))
        return True

    try:
        name = job(tmp_path, "j1", created=time.time() - 3000, log_age=queue_doctor.QUIET_ALERT_S + 60, process=process)
        assert queue_doctor.sweep(jobs, sleep=lambda s: None, tell=tell) == []
        assert process.poll() is None and jobs.read(name)["state"] == "running"
        assert [(n, k) for n, k, _ in told] == [(name, "quiet")] and "quiet" in queue_notice.NOTE_ONLY
        assert "QUIET_ALERT_S = 900 s" in told[0][2] and "a notice only" in told[0][2]
    finally:
        process.kill()


def test_a_job_whose_runner_is_gone_is_recorded_as_failed(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    name = job(tmp_path, "j1", created=time.time() - 30, log_age=1, process=process)
    process.kill()
    process.wait(timeout=10)
    assert queue_doctor.sweep(jobs, sleep=lambda s: None) == [name]
    status = jobs.read(name)
    assert status["state"] == "failed" and "its runner process is gone" in status["detail"]


def test_a_local_process_job_survives_a_doctor_sweep(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        name = job(tmp_path, "local", created=time.time(), log_age=1, process=process)
        jobs.write(name, "spec.json", {"id": name, "owner": {"id": "", "source": "local-process",
                                                                  "pid": os.getpid(), "started": storage.process_start(os.getpid())}})
        assert queue_doctor.sweep(jobs, sleep=lambda _: None) == []
        assert process.poll() is None and jobs.read(name)["state"] == "running"
    finally:
        process.kill()


def test_a_job_with_no_owner_is_not_signalled(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        name = job(tmp_path, "j0", created=time.time(), log_age=1, process=process)
        (tmp_path / "jobs" / name / "spec.json").write_text(json.dumps({"id": name, "created": time.time(), "owner": {}}))
        said = []
        assert queue_doctor.sweep(jobs, say=said.append, sleep=lambda s: None) == []
        assert process.poll() is None
        assert jobs.read(name)["state"] == "running" and any("it has no owner" in line for line in said)
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


def test_a_job_older_than_the_mark_keeps_running_and_its_owner_is_told_how_long_it_ran(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    told = []
    try:
        job(tmp_path, "j3", created=time.time() - queue_doctor.AGE_ALERT_S - 60, log_age=5, process=process)
        assert queue_doctor.sweep(jobs, sleep=lambda s: None, tell=lambda *a, **k: told.append(a[2:4]) or True) == []
        assert process.poll() is None and [kind for kind, _ in told] == ["old"] and "AGE_ALERT_S = 5400 s" in told[0][1]
    finally:
        process.kill()


def test_a_reused_pid_is_never_signalled(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        name = job(tmp_path, "j4", created=time.time() - 3000, log_age=queue_doctor.QUIET_ALERT_S + 60, process=process)
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
        name = job(tmp_path, "j6", created=time.time() - 3000, log_age=5, process=process)
        (tmp_path / "jobs" / name / "spec.json").write_text(json.dumps({"id": name, "created": time.time(), "owner": {}}))
        said = []
        assert queue_doctor.sweep(jobs, say=said.append, sleep=lambda s: None) == []
        assert process.poll() is None
        assert any("stray.dir" in line for line in said)
    finally:
        process.kill()


def test_an_old_job_that_is_still_printing_is_left_alone(tmp_path):
    jobs = testjobs.Jobs(tmp_path)
    process = sleeper()
    try:
        job(tmp_path, "j7", created=time.time() - queue_doctor.AGE_ALERT_S - 600, log_age=5, process=process)
        assert queue_doctor.sweep(jobs, sleep=lambda s: None, tell=lambda *a, **k: True) == []
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


class Unreadable:
    """A process whose attributes macOS will not release, as psutil reports one (a system error rather than a psutil error)."""

    pid = 2**22 + 1

    def as_dict(self, attrs=None, ad_value=None):
        raise SystemError("error return without exception set")


def test_an_unreadable_process_does_not_stop_the_pass_that_ends_old_nodes(monkeypatch, tmp_path):
    program = node_program(tmp_path)
    process = subprocess.Popen([str(program), "run", "--state", str(tmp_path / "state")], start_new_session=True)
    try:
        time.sleep(0.5)

        def process_iter(attrs=None, ad_value=None):
            if attrs:
                raise PermissionError("sysctl KERN_PROCARGS2")
            yield Unreadable()
            yield psutil.Process(process.pid)

        monkeypatch.setattr(queue_doctor.psutil, "process_iter", process_iter)
        old = time.time() + queue_doctor.NODE_MAX_AGE_S + 60
        assert queue_doctor.reap_nodes(now=old, say=lambda text: None, only=frozenset({process.pid})) == [process.pid]
        assert process.wait(timeout=10) is not None
    finally:
        process.kill()

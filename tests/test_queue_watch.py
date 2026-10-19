"""The queue doctor runs inside the broker: it ends an agent's job that is too long, tells the board once, leaves a person's run alone, and every status names the longest run first."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import queue_doctor  # noqa: E402
import queue_notice  # noqa: E402
import testjobs  # noqa: E402
import testqueue  # noqa: E402
import testreuse_store as storage  # noqa: E402

from poolhouse.workspace import testboard  # noqa: E402

pytest_plugins = ["node_kit"]
AGENT = {"source": "workspace-session"}
PERSON = {"id": "person", "source": "terminal"}


def make_job(folder: Path, name: str, owner: dict, process: subprocess.Popen | None = None, **options) -> str:
    """A running job record; ``options`` are estimate, workers, ran (seconds since it started), quiet (seconds its log is quiet) and argv."""
    where = folder / "jobs" / name
    where.mkdir(parents=True)
    pid = process.pid if process else 0
    ran = options.get("ran", 60.0)
    status = {"state": "running", "pid": pid, "started": storage.process_start(pid) if pid else 0.0, "at": time.time() - ran}
    if options.get("estimate") is not None:
        status.update(estimate_s=options["estimate"], workers=options.get("workers", 15))
    (where / "status.json").write_text(json.dumps(status))
    (where / "spec.json").write_text(json.dumps({"id": name, "argv": list(options.get("argv", ("full",))), "created": time.time() - ran,
                                                 "root": str(ROOT), "owner": owner}))
    (where / "log").write_text("x")
    os.utime(where / "log", (time.time() - options.get("quiet", 5.0),) * 2)
    return name


def sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], start_new_session=True)


@pytest.fixture
def board(workspace_node, monkeypatch):
    """A real node with an owner of the job and the session of the process that runs the doctor."""
    owner, doctor = workspace_node.member("job-owner"), workspace_node.member("doctor-process")
    monkeypatch.setattr(testboard, "acting", lambda: workspace_node.session(doctor))
    return workspace_node, owner, doctor


def words(entries) -> str:
    return " ".join(entry.fields["body"] for entry in entries)


def test_an_agent_job_estimated_at_two_hours_is_ended_and_the_board_names_it(tmp_path, board):
    node, owner, doctor = board
    jobs, process = testjobs.Jobs(tmp_path), sleeper()
    try:
        name = make_job(tmp_path, "j1", {"id": owner.name, **AGENT}, estimate=7200.0 * 1.4, process=process)
        said = []
        assert queue_doctor.sweep(jobs, say=said.append, sleep=lambda s: process.wait(timeout=10)) == [name]
        assert process.poll() is not None
        assert "ended by the queue doctor: estimated 2.8 h on 15 workers: a full suite; GitHub runs those" in jobs.read(name)["detail"]
        inbox = words(node.session(owner).read_all(inbox=True))
        announced = words(node.session(doctor).read_all(channel="#announcements"))
        for text in (inbox, announced):
            assert name in text and owner.name in text and "estimated 2.8 h on 15 workers: a full suite; GitHub runs those" in text
        queue_notice.post(jobs, name, "ended", "ended by the queue doctor: again", say=said.append)
        assert words(node.session(owner).read_all(inbox=True)).count(name) == 1
        assert words(node.session(doctor).read_all(channel="#announcements")).count(name) == 1
    finally:
        process.kill()
        process.wait(timeout=5)


def test_a_persons_run_is_never_ended(tmp_path, board):
    jobs, process = testjobs.Jobs(tmp_path), sleeper()
    try:
        make_job(tmp_path, "j1", PERSON, estimate=9000.0, quiet=queue_doctor.QUIET_S + 600, ran=99999.0, process=process)
        assert queue_doctor.sweep(jobs, sleep=lambda s: None) == []
        assert process.poll() is None
        assert queue_doctor.hung(jobs, "j1", time.time()) == ""
    finally:
        process.kill()
        process.wait(timeout=5)


def test_an_agent_job_twenty_minutes_past_its_estimate_is_ended(tmp_path, board):
    jobs, process = testjobs.Jobs(tmp_path), sleeper()
    try:
        name = make_job(tmp_path, "j1", {"id": board[1].name, **AGENT}, estimate=600.0, ran=600.0 + queue_doctor.OVERRUN_S + 60,
                        argv=("all", "tests/test_x.py"), process=process)
        assert queue_doctor.sweep(jobs, sleep=lambda s: process.wait(timeout=10)) == [name]
        assert "past its estimate of 10 min" in jobs.read(name)["detail"]
    finally:
        process.kill()
        process.wait(timeout=5)


def test_a_job_inside_its_estimate_is_left_running(tmp_path, board):
    jobs, process = testjobs.Jobs(tmp_path), sleeper()
    try:
        make_job(tmp_path, "j1", {"id": board[1].name, **AGENT}, estimate=900.0, ran=1000.0, process=process)
        assert queue_doctor.sweep(jobs, sleep=lambda s: None) == []
    finally:
        process.kill()
        process.wait(timeout=5)


def test_a_run_admitted_over_ten_minutes_is_announced_once_and_over_twenty_is_ended(tmp_path, board):
    node, owner, doctor = board
    jobs = testjobs.Jobs(tmp_path)
    make_job(tmp_path, "slow", {"id": owner.name, **AGENT}, argv=("all", "tests/test_x.py"))
    assert queue_doctor.admitted(jobs, "slow", 900.0, 4) == ""
    assert queue_doctor.admitted(jobs, "slow", 900.0, 4) == ""
    assert jobs.read("slow")["estimate_s"] == 900.0
    assert f"test job slow (owner {owner.name}) admitted: estimated 15 min on 4 workers" in words(node.session(owner).read_all(inbox=True))
    assert words(node.session(doctor).read_all(channel="#announcements")).count("slow") == 1
    make_job(tmp_path, "huge", {"id": owner.name, **AGENT})
    assert "estimated 2.8 h on 15 workers: a full suite; GitHub runs those" in queue_doctor.admitted(jobs, "huge", 10080.0, 15)
    make_job(tmp_path, "short", {"id": owner.name, **AGENT}, argv=("all", "tests/test_x.py"))
    assert queue_doctor.admitted(jobs, "short", 60.0, 2) == ""
    assert "short" not in words(node.session(owner).read_all(inbox=True))


def test_a_person_run_admitted_over_ten_minutes_posts_nothing(tmp_path, board):
    node, owner, _ = board
    jobs = testjobs.Jobs(tmp_path)
    make_job(tmp_path, "mine", PERSON)
    assert queue_doctor.admitted(jobs, "mine", 90000.0, 15) == ""
    assert node.session(owner).read_all(inbox=True) == []


def test_the_doctor_passes_at_most_once_in_ten_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_TEST_REUSE_DIR", str(tmp_path / "reuse"))
    passes = []
    monkeypatch.setattr(queue_doctor, "mend", lambda root, say, now: passes.append(now) or [])
    queue_doctor.check(tmp_path, now=1000.0)
    queue_doctor.check(tmp_path, now=1005.0)
    queue_doctor.check(tmp_path, now=1011.0)
    assert passes == [1000.0, 1011.0]


def run_record(directory: Path, label: str, estimate: float, owner: str, state: str = "running") -> testqueue.RunRecord:
    record = testqueue.RunRecord(directory, label, "interactive", estimate, 4)
    record.update(owner=owner, state=state, granted=4 if state == "running" else 0, started=time.time() if state == "running" else None)
    return record


def environment(tmp_path: Path) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE"))}
    return {**base, "DEV_TEST_SLOTS_DIR": str(tmp_path / "slots"), "DEV_TEST_REUSE_DIR": str(tmp_path / "reuse")}


@pytest.mark.parametrize("command", [["scripts/testslots.py", "status"], ["scripts/test", "status"]])
def test_every_status_starts_with_the_longest_run(tmp_path, command):
    directory = tmp_path / "slots"
    directory.mkdir()
    small = run_record(directory, "small run", 30.0, "agent-small")
    big = run_record(directory, "big run", 10080.0, "agent-big", state="queued")
    try:
        done = subprocess.run([sys.executable, str(ROOT / command[0]), *command[1:]], cwd=ROOT, env=environment(tmp_path),
                              capture_output=True, text=True, timeout=120, check=False)
    finally:
        small.close()
        big.close()
    assert done.returncode == 0, done.stderr
    first = done.stdout.splitlines()[0]
    assert first.startswith("longest run: queued") and "owner agent-big" in first and "est 10080s" in first and "big run" in first, done.stdout


def test_a_scripts_test_call_runs_the_doctor_and_ends_an_agents_two_hour_job(tmp_path):
    from poolhouse.workspace import testruns
    env = environment(tmp_path)
    store = Path(env["DEV_TEST_REUSE_DIR"]) / testruns.scope(ROOT)
    process = sleeper()
    try:
        name = make_job(store, "j1", {"id": "claude-aaaaaa", **AGENT}, estimate=10080.0, process=process)
        done = subprocess.run([sys.executable, str(ROOT / "scripts/test"), "status"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=120, check=False)
        assert done.returncode == 0, done.stderr
        assert "queue doctor: ended job j1: estimated 2.8 h on 15 workers: a full suite; GitHub runs those" in done.stderr
        assert "no board identity here, so the ended notice for job j1 is not sent" in done.stderr
        assert process.wait(timeout=20) is not None
        assert testjobs.Jobs(store).read(name)["state"] == "failed"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

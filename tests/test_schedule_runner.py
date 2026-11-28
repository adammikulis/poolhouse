"""One job's process: waiting for its time and for the device, running its steps, recording what came of each, ending missed,
cancelled or interrupted, and telling whoever scheduled it.

The broker and the lease table are real (a test's own node), and the bench steps are real ``python -m poolhouse.bench`` reports
over an empty store, which load nothing. The clock is the test's, so a window of hours passes at once; no model runs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from reservation_kit import Wire

from poolhouse import caller, schedule_record as record
from poolhouse.schedule import device as device_module, execute, notice, runner
from poolhouse.schedule.device import Device
from poolhouse.serve import broker_holds, gpu
from poolhouse.serve.broker import Broker
from poolhouse.serve.process import started_at

pytest_plugins = ["serve_node", "reservation_kit"]
pytestmark = [pytest.mark.usefixtures("serve_leases"), pytest.mark.slow]
REPO = Path(__file__).resolve().parents[1]
REPORT = ["quants", "report", "--set", "qwen3.8-27b"]


class Clock:
    """A time the test moves: sleeping passes it at once."""

    def __init__(self) -> None:
        self.now = time.time()
        self.slept = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.slept += seconds


class Told:
    def __init__(self, error: str = "") -> None:
        self.jobs, self.error = [], error

    def __call__(self, job):
        self.jobs.append(job)
        return self.error


class NoSleepAssertion:
    def hold(self, how):
        pass

    def release(self):
        pass


def bench(n: int, argv: list[str], role: str = "step") -> dict:
    return {"n": n, "line": n, "kind": "bench", "role": role, "words": " ".join(argv)[:60], "argv": argv}


def job(steps: list[dict], *, at: float, latest: float) -> dict:
    """A kept job whose bench steps run this checkout's code."""
    def code(where: Path) -> None:
        (where / "code").mkdir()
        (where / "code" / "src").symlink_to(REPO / "src")

    return record.create({"name": "night", "at": at, "latest": latest, "max_hours": 1.0, "created": time.time(), "steps": steps,
                          "by": {"who": "user tester", "board": "b", "agent": "claude-lead", "cwd": str(REPO)},
                          "code": {"python": sys.executable, "copied": True}, "pins": {}, "child": None}, code)


def drive(made: dict, broker: Broker, clock: Clock | None = None, told: Told | None = None, selection=lambda: None) -> str:
    world = runner.World(clock=clock or time.time, sleep=clock.sleep if clock else time.sleep, tell=told or Told(),
                         selection=selection, look_s=0.05)
    device = Device(made["id"], wire=Wire(broker, os.getpid()), measuring=lambda: None)
    return runner.Runner(made["id"], world, device=device, awake=NoSleepAssertion()).run()


def events(name: str) -> list[dict]:
    return [row for row in caller.audit_rows() if row["event"] == name]


def test_a_free_device_runs_the_steps_in_order_and_records_each_with_the_smoke_rule_and_the_removal_gate(device_broker):
    broker, _process = device_broker
    now = time.time()
    made = job([bench(1, ["quants", "report", "--set", "no-such-set"], "smoke"), bench(2, REPORT, "then"), bench(3, REPORT),
                {"n": 4, "line": 4, "kind": "remove-model", "role": "step", "words": "remove-model", "ref": "hf:a/b/c.gguf"}],
               at=now - 1, latest=now + 600)
    told = Told()
    assert drive(made, broker, told=told) == "failed"
    kept = record.load(made["id"])
    assert [kept["results"][str(n)]["state"] for n in (1, 2, 3, 4)] == ["failed", "skipped", "ok", "skipped"], kept["results"]
    assert "step 1" in kept["results"]["2"]["why"] and "every step before it passed" in kept["results"]["4"]["why"]
    output = record.log_tail(made["id"])
    assert "nothing kept yet" in output and "python -m poolhouse.bench quants report --set qwen3.8-27b" in output
    assert kept["started"] and kept["ended"] and kept["child"] is None and broker_holds.JOB_CLAIM not in broker.claims
    assert [one["state"] for one in told.jobs] == ["failed"] and kept["notice"]["sent"]
    assert events("schedule.run")[0]["agent"] == "claude-lead" and events("schedule.end")[0]["state"] == "failed"


def test_a_job_whose_steps_pass_ends_done_and_holds_the_device_while_a_step_runs(device_broker, monkeypatch):
    broker, _process = device_broker
    now = time.time()
    made = job([bench(1, REPORT), bench(2, REPORT)], at=now - 1, latest=now + 600)
    held: list[bool] = []
    ran = execute.bench

    def watched(run, step):
        held.append(broker.claims.get(broker_holds.JOB_CLAIM, {}).get("pid") == os.getpid())
        return ran(run, step)

    monkeypatch.setitem(runner.STEPS, "bench", watched)
    assert drive(made, broker) == "done"
    assert held == [True, True] and record.load(made["id"])["why"] == "" and not broker.claims


def test_it_waits_for_its_time_before_asking_for_the_device(device_broker):
    broker, _process = device_broker
    clock = Clock()
    made = job([bench(1, REPORT)], at=clock.now + 3600, latest=clock.now + 7200)
    asked: list[float] = []
    claim = broker.claim
    broker.claim = lambda *a, **k: asked.append(clock.now) or claim(*a, **k)
    assert drive(made, broker, clock) == "done"
    assert asked and min(asked) >= made["at"] and clock.slept >= 3600


def test_a_busy_device_is_waited_for_with_the_reason_and_the_job_is_missed_at_the_latest_start(device_broker):
    broker, process = device_broker
    other = process().pid
    broker.claim(gpu.CLAIM, other, {"purpose": "a long training run"})
    clock = Clock()
    made = job([bench(1, REPORT)], at=clock.now, latest=clock.now + 600)
    seen: list[str] = []
    sleep = clock.sleep
    clock.sleep = lambda seconds: (seen.append(record.load(made["id"])["why"]), sleep(seconds))
    told = Told()
    assert drive(made, broker, clock, told) == "missed"
    kept = record.load(made["id"])
    assert "a long training run" in seen[0] and str(other) in seen[0]
    assert "the device was not free" in kept["why"] and "a long training run" in kept["why"]
    assert kept["results"] == {} and [one["state"] for one in told.jobs] == ["missed"]
    assert broker.claims[gpu.CLAIM]["pid"] == other, "the work in the way is left as it was"


def test_a_time_that_passed_with_nothing_watching_is_reported_missed(device_broker):
    broker, _process = device_broker
    now = time.time()
    made = job([bench(1, REPORT)], at=now - 7200, latest=now - 3600)
    assert drive(made, broker) == "missed"
    assert "asleep or off" in record.load(made["id"])["why"] and not broker.claims


def test_a_cancel_before_the_start_ends_it_without_running(device_broker):
    broker, _process = device_broker
    now = time.time()
    made = job([bench(1, REPORT)], at=now + 3600, latest=now + 7200)
    record.update(made["id"], lambda kept: kept.update(cancel={"by": "user tester", "at": now}))
    assert drive(made, broker) == "cancelled"
    assert record.load(made["id"])["results"] == {}


def test_a_cancel_while_a_step_runs_ends_that_steps_processes(device_broker, monkeypatch):
    broker, _process = device_broker
    now = time.time()
    made = job([bench(1, REPORT), bench(2, REPORT)], at=now - 1, latest=now + 600)
    pids: list[int] = []

    def long(run, _step):
        child = run.child
        run.child = lambda pid: (pids.append(pid) if pid else None, child(pid))
        threading.Timer(0.5, record.cancel, (made["id"], "user tester")).start()
        return execute._ended(*run.command([sys.executable, "-c", "import time;time.sleep(60)"], cwd=run.folder / "work",
                                           env=dict(os.environ)))

    monkeypatch.setitem(runner.STEPS, "bench", long)
    began = time.monotonic()
    assert drive(made, broker) == "cancelled"
    kept = record.load(made["id"])
    assert time.monotonic() - began < 30 and kept["results"]["1"]["state"] == "failed" and "2" not in kept["results"]
    assert pids and started_at(pids[0]) is None and not broker.claims


def test_a_job_past_its_time_limit_stops_its_step(device_broker, monkeypatch):
    broker, _process = device_broker
    now = time.time()
    made = job([bench(1, REPORT)], at=now - 1, latest=now + 600)
    record.update(made["id"], lambda kept: kept.update(max_hours=1 / 3600))
    monkeypatch.setitem(runner.STEPS, "bench", lambda run, _step: execute._ended(*run.command(
        [sys.executable, "-c", "import time;time.sleep(60)"], cwd=run.folder / "work", env=dict(os.environ))))
    assert drive(made, broker) == "failed"
    assert "ran past its" in record.load(made["id"])["why"]


def test_a_job_whose_process_ended_while_it_ran_is_failed_and_nothing_is_run_again(device_broker):
    broker, _process = device_broker
    now = time.time()
    made = job([bench(1, REPORT), bench(2, REPORT)], at=now - 60, latest=now + 600)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    record.update(made["id"], lambda kept: kept.update(
        state="running", started=now - 30, results={"1": {"state": "running", "why": "", "seconds": 0}},
        child={"pid": gone.pid, "started": now - 30}))
    assert drive(made, broker) == "failed"
    kept = record.load(made["id"])
    assert "during step 1" in kept["why"] and "nothing is run again" in kept["why"]
    assert kept["results"]["1"]["state"] == "failed" and "2" not in kept["results"] and "quants" not in record.log_tail(made["id"])


def installed(tmp_path: Path, name: str, *, schedules: bool) -> Path:
    """The prefix of an installed runtime as its files show it."""
    package = tmp_path / name / "lib" / "python3.13" / "site-packages" / "poolhouse"
    (package / "schedule").mkdir(parents=True) if schedules else package.mkdir(parents=True)
    if schedules:
        (package / "schedule" / "cli.py").touch()
    return tmp_path / name


def test_a_waiting_job_hands_over_to_a_newly_selected_runtime_and_stays_waiting(device_broker, tmp_path):
    broker, _process = device_broker
    clock = Clock()
    made = job([bench(1, REPORT)], at=clock.now + 3600, latest=clock.now + 7200)
    selected = iter([installed(tmp_path, "old", schedules=True), installed(tmp_path, "new", schedules=True)])
    assert drive(made, broker, clock, selection=lambda: next(selected)) == "handover"
    assert record.load(made["id"])["state"] == "waiting"


@pytest.mark.parametrize("near", [False, True])
def test_a_waiting_job_keeps_its_process_for_a_runtime_without_scheduled_jobs_and_close_to_its_time(device_broker, tmp_path, near):
    broker, _process = device_broker
    clock = Clock()
    early = 120 if near else 3600
    made = job([bench(1, REPORT)], at=clock.now + early, latest=clock.now + 7200)
    old, new = installed(tmp_path, "old", schedules=True), installed(tmp_path, "new", schedules=near)
    looked: list[Path] = []

    def selection() -> Path:
        looked.append(old if not looked else new)
        return looked[-1]

    assert drive(made, broker, clock, selection=selection) == "done"


def full_disk(monkeypatch, refused) -> list[str]:
    """Refuse the record writes for which ``refused(record, writes so far)`` holds, as a full disk does."""
    real, seen = record._write, []

    def write(where, kept):
        seen.append(kept["state"])
        if refused(kept, len(seen)):
            raise OSError(28, "No space left on device")
        real(where, kept)

    monkeypatch.setattr(record, "_write", write)
    return seen


@pytest.mark.parametrize("refused", [2, 3, 4, 5])
def test_a_record_that_cannot_be_written_once_neither_ends_the_job_nor_kills_its_step(device_broker, monkeypatch, refused):
    broker, _process = device_broker
    clock = Clock()
    made = job([bench(1, REPORT), bench(2, REPORT)], at=clock.now - 1, latest=clock.now + 600)
    writes: list[int] = []

    def once(kept, _count):
        if kept["state"] == "running":
            writes.append(1)
        return kept["state"] == "running" and len(writes) == refused

    full_disk(monkeypatch, once)
    told = Told()
    assert drive(made, broker, told=told) == "done"
    kept = record.load(made["id"])
    assert kept["state"] == "done" and [kept["results"][n]["state"] for n in ("1", "2")] == ["ok", "ok"], kept
    assert [one["state"] for one in told.jobs] == ["done"] and "could not be written" in record.log_tail(made["id"])


def test_a_start_that_cannot_be_recorded_is_tried_again_and_nothing_runs_unrecorded(device_broker, monkeypatch):
    broker, _process = device_broker
    clock = Clock()
    made = job([bench(1, REPORT)], at=clock.now, latest=clock.now + 600)
    failed: list[int] = []
    ran: list[str] = []
    real = execute.bench
    monkeypatch.setitem(runner.STEPS, "bench",
                        lambda run, step: (ran.append(record.load(made["id"])["state"]), real(run, step))[1])

    def twice(kept, _count):
        if kept["state"] == "running" and not kept["results"] and len(failed) < 2:
            failed.append(1)
            return True
        return False

    full_disk(monkeypatch, twice)
    assert drive(made, broker, clock) == "done"
    assert len(failed) == 2 and ran == ["running"]


def test_an_end_that_cannot_be_written_is_told_at_once_and_written_when_the_disk_has_room(device_broker, monkeypatch):
    broker, _process = device_broker
    clock = Clock()
    made = job([bench(1, REPORT)], at=clock.now - 1, latest=clock.now + 600)
    failed: list[int] = []

    def for_a_while(kept, _count):
        if kept["state"] == "done" and len(failed) < 6:
            failed.append(1)
            return True
        return False

    full_disk(monkeypatch, for_a_while)
    told = Told()
    assert drive(made, broker, clock, told) == "done"
    assert record.load(made["id"])["state"] == "done" and len(told.jobs) == 1 and not broker.claims


def measured(rows: list[tuple[str, int]]):
    """A measuring step as the runner sees it. PLUMBING: it leaves the receipt a bench step's kept runs leave; nothing is measured."""
    def step(run, step_):
        receipt = run.folder / f"kept-{step_['n']}.jsonl"
        receipt.write_text("".join(json.dumps({"label": label, "rows": 4, "usable": usable}) + "\n" for label, usable in rows),
                           encoding="utf-8")
        return "ok", ""

    return step


def removal_after(*before: dict) -> list[dict]:
    return [*before, {"n": len(before) + 1, "line": 9, "kind": "remove-model", "role": "step", "words": "remove-model",
                      "ref": "hf:a/b/c.gguf"}]


@pytest.mark.parametrize(("rows", "why"), [
    ([], "kept no result"), ([("set:one", 4), ("set:two", 0)], "nothing usable for set:two")])
def test_a_model_stays_when_a_measuring_step_before_it_kept_nothing_usable(device_broker, monkeypatch, rows, why):
    broker, _process = device_broker
    now = time.time()
    made = job(removal_after({**bench(1, ["quants", "speed"]), "measures": True}), at=now - 1, latest=now + 600)
    monkeypatch.setitem(runner.STEPS, "bench", measured(rows))
    assert drive(made, broker) == "failed"
    kept = record.load(made["id"])
    assert kept["results"]["2"]["state"] == "skipped" and why in kept["results"]["2"]["why"], kept["results"]
    assert why in kept["results"]["1"]["why"] and why in kept["why"]


def test_a_report_that_found_nothing_is_no_reason_to_remove_a_model(device_broker):
    broker, _process = device_broker
    now = time.time()
    made = job(removal_after({**bench(1, REPORT), "measures": False}), at=now - 1, latest=now + 600)
    assert drive(made, broker) == "failed"
    kept = record.load(made["id"])
    assert kept["results"]["1"]["state"] == "ok" and kept["results"]["2"]["state"] == "skipped"
    assert "no step before it kept a result" in kept["results"]["2"]["why"]


def test_a_removal_after_a_usable_measurement_is_run(device_broker, monkeypatch):
    broker, _process = device_broker
    now = time.time()
    made = job(removal_after({**bench(1, ["quants", "speed"]), "measures": True}), at=now - 1, latest=now + 600)
    monkeypatch.setitem(runner.STEPS, "bench", measured([("set:one", 3)]))
    assert drive(made, broker) == "failed"
    results = record.load(made["id"])["results"]
    assert results["1"]["usable"] is True
    assert results["2"]["state"] == "failed" and "holds no a/b/c.gguf" in results["2"]["why"], "the removal itself ran"


def test_what_holds_the_device_is_named_without_taking_it(device_broker):
    broker, process = device_broker
    other = process().pid
    broker.claim(gpu.CLAIM, other, {"purpose": "a long training run"})
    found = device_module.blockers(wire=Wire(broker, os.getpid()), measuring=lambda: None)
    assert len(found) == 1 and "a long training run" in found[0] and str(other) in found[0]
    assert set(broker.claims) == {gpu.CLAIM}
    broker.unclaim(gpu.CLAIM, other)
    assert device_module.blockers(wire=Wire(broker, os.getpid()), measuring=lambda: None) == []


def test_a_notice_that_cannot_be_sent_is_kept_to_try_again(device_broker):
    broker, _process = device_broker
    now = time.time()
    made = job([bench(1, REPORT)], at=now - 7200, latest=now - 3600)
    drive(made, broker, told=Told("the node did not answer"))
    kept = record.load(made["id"])
    assert not kept["notice"]["sent"] and kept["notice"]["error"] == "the node did not answer"
    again = Told()
    drive(made, broker, told=again)
    assert record.load(made["id"])["notice"]["sent"] and len(again.jobs) == 1 and len(events("schedule.end")) == 1


def test_a_changed_input_file_fails_the_step_before_it_runs(device_broker, tmp_path):
    broker, _process = device_broker
    now = time.time()
    made = job([bench(1, REPORT)], at=now - 1, latest=now + 600)
    held = tmp_path / "model.gguf"
    held.write_bytes(b"x")
    record.update(made["id"], lambda kept: kept.update(pins={str(held): [0, 0, 0]}))
    assert drive(made, broker) == "failed"
    assert "is not the file it was" in record.load(made["id"])["results"]["1"]["why"]
    assert "nothing kept yet" not in record.log_tail(made["id"])


def test_the_notice_says_how_the_job_ended_and_how_to_read_it():
    said = notice.message({"id": "s-20261128-0a0b0c0d", "name": "night", "at": 0, "state": "missed", "why": "asleep",
                           "results": {"1": {"state": "ok"}, "2": {"state": "skipped"}}})
    assert "ended missed: asleep" in said and "1 ok, 1 skipped" in said
    assert "poolhouse schedule show s-20261128-0a0b0c0d" in said

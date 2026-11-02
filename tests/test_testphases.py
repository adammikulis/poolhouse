"""The phase line and its warning thresholds."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import testphases
from testphases import Phases


def test_a_run_with_small_overhead_prints_no_warning():
    text = testphases.line(Phases(wall=12.0, collection=1.0, setup=0.5, tests=10.0, teardown=0.5))
    assert "collection 1.0 s, worker setup 0.5 s, tests 10.0 s, teardown 0.5 s" in text
    assert "WARNING" not in text


def test_more_than_five_seconds_of_fixed_overhead_warns():
    assert "WARNING" in testphases.line(Phases(wall=100.0, collection=3.0, setup=3.0, tests=94.0, teardown=0.0))


def test_a_quarter_of_wall_time_warns_once_it_is_over_the_floor():
    assert testphases.too_slow(Phases(wall=12.0, collection=2.0, setup=2.0, tests=8.0, teardown=0.0))
    assert not testphases.too_slow(Phases(wall=2.0, collection=1.5, setup=0.4, tests=0.1, teardown=0.0))


def test_the_recorder_writes_phases_the_runner_reads(tmp_path):
    target = tmp_path / "phases.json"
    (tmp_path / "test_one.py").write_text("def test_one():\n    assert True\n")
    env = {**os.environ, testphases.PHASES_ENV: str(target), "PYTHONPATH": str(Path(testphases.__file__).parent)}
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "testphases", "-p", "no:cacheprovider",
                           str(tmp_path / "test_one.py")], cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    phases = testphases.read(target)
    assert phases is not None
    assert phases.wall >= phases.tests >= 0.0


def test_time_queued_for_cpu_permits_is_not_fixed_overhead():
    phases = Phases(wall=30.0, collection=2.0, setup=1.0, tests=2.0, teardown=1.0, queued=24.0)
    assert phases.fixed == 4.0
    assert not testphases.too_slow(phases)
    assert "queued for CPU 24.0 s" in testphases.line(phases)


def test_phases_written_without_a_queued_figure_still_read(tmp_path):
    target = tmp_path / "phases.json"
    target.write_text('{"wall": 5.0, "collection": 1.0, "setup": 1.0, "tests": 2.0, "teardown": 1.0}')
    assert testphases.read(target) == Phases(5.0, 1.0, 1.0, 2.0, 1.0, 0.0)


def report(when: str, worker: str, waited: float | None = None, duration: float = 0.0):
    node = SimpleNamespace(gateway=SimpleNamespace(id=worker))
    properties = [] if waited is None else [(testphases.WAIT_PROPERTY, waited)]
    return SimpleNamespace(when=when, node=node, duration=duration, start=1.0, user_properties=properties)


def test_the_recorder_uses_the_same_workers_permit_wait(tmp_path):
    recorder = testphases.Recorder(str(tmp_path / "phases.json"))
    for worker, waited in (("gw0", 6.0), ("gw1", 2.0)):
        recorder.pytest_runtest_logreport(report("setup", worker, waited))
        recorder.pytest_runtest_logreport(report("call", worker, duration=1.0))
        recorder.pytest_runtest_logreport(report("teardown", worker))
    recorder.pytest_runtest_logreport(report("setup", "gw0", 4.0))
    recorder.pytest_sessionfinish(None)
    assert testphases.read(tmp_path / "phases.json").queued == 10.0


def test_tests_are_the_busiest_workers_calls_so_one_long_test_is_not_fixed_overhead(tmp_path):
    recorder = testphases.Recorder(str(tmp_path / "phases.json"))
    for worker, seconds in (("gw0", 20.0), ("gw1", 1.0), ("gw2", 1.0)):
        recorder.pytest_runtest_logreport(report("setup", worker, 0.0))
        recorder.pytest_runtest_logreport(report("call", worker, duration=seconds))
        recorder.pytest_runtest_logreport(report("teardown", worker))
    recorder.pytest_sessionfinish(None)
    assert testphases.read(tmp_path / "phases.json").tests == 20.0


def test_a_test_carries_the_seconds_it_waited_for_admission(monkeypatch):
    import contextlib

    import testslots_pytest

    @contextlib.contextmanager
    def request(operation, **fields):
        time.sleep(0.2)
        yield {}

    monkeypatch.setattr(testslots_pytest.testslots_rpc, "request", request)
    testslots_pytest.take_waited()
    with testslots_pytest.slot(label="one"):
        pass
    with testslots_pytest.slot(label="two"):
        pass
    assert 0.4 <= testslots_pytest.take_waited() < 5.0
    assert testslots_pytest.take_waited() == 0.0


def recorded(tmp_path, monkeypatch, rows, *, wall):
    """Feed reports with a deterministic controller span, without launching workers."""
    monkeypatch.setattr(testphases.time, "time", lambda: 0.0)
    target = tmp_path / "timing.json"
    recorder = testphases.Recorder(str(target))
    for when, worker, duration, waited in rows:
        recorder.pytest_runtest_logreport(report(when, worker, waited, duration))
    monkeypatch.setattr(testphases.time, "time", lambda: wall)
    recorder.pytest_sessionfinish(None)
    return testphases.read(target)


def test_serial_fixture_totals_include_later_tests(tmp_path, monkeypatch):
    rows = [(phase, "main", duration, None) for phase, duration in
            (("setup", 1), ("call", 2), ("teardown", 1),
             ("setup", 3), ("call", 2), ("teardown", 2))]
    phases = recorded(tmp_path, monkeypatch, rows, wall=13)
    assert (phases.collection, phases.setup, phases.tests, phases.teardown, phases.residual) == (1, 4, 4, 3, 1)
    assert phases.fixed == 9
    assert "worker setup 4.0 s" in testphases.line(phases)
    assert "WARNING fixed overhead" in testphases.line(phases)


def test_parallel_breakdown_never_mixes_workers_or_sums_parallel_calls(tmp_path, monkeypatch):
    rows = [(phase, worker, duration, waited) for worker, setup, call, teardown, waited in
            (("gw0", 1, 7, 1, 0.2), ("gw1", 6, 4, 2, 3))
            for phase, duration in (("setup", setup), ("call", call), ("teardown", teardown))]
    # Admission wait is emitted only on setup reports.
    rows = [(phase, worker, duration, waited if phase == "setup" else None)
            for phase, worker, duration, waited in rows]
    phases = recorded(tmp_path, monkeypatch, rows, wall=14)
    assert (phases.worker, phases.setup, phases.tests, phases.teardown, phases.queued) == ("gw1", 6, 4, 2, 3)
    assert phases.residual == 1
    assert phases.fixed == 7
    assert phases.workers["gw0"]["call"] == 7
    assert phases.workers["gw1"]["call"] == 4
    assert '"gw0": {"call": 7.0' in testphases.line(phases)
    assert "WARNING fixed overhead" in testphases.line(phases)


def test_incomplete_worker_evidence_warns_without_hiding_fixed_cost(tmp_path, monkeypatch):
    phases = recorded(tmp_path, monkeypatch, [("setup", "gw0", 8, 0)], wall=10)
    assert not phases.complete
    assert phases.fixed == 10
    assert "WARNING phase accounting" in testphases.line(phases)
    assert "WARNING fixed overhead" in testphases.line(phases)


def test_impossible_report_span_warns_instead_of_clamping_residual(tmp_path, monkeypatch):
    rows = [(phase, "main", 4, None) for phase in ("setup", "call", "teardown")]
    phases = recorded(tmp_path, monkeypatch, rows, wall=10)
    assert phases.residual == -3
    assert "WARNING phase accounting" in testphases.line(phases)


def test_missing_worker_identity_is_explicitly_unknown(tmp_path, monkeypatch):
    rows = [(phase, None, 1, None) for phase in ("setup", "call", "teardown")]
    phases = recorded(tmp_path, monkeypatch, rows, wall=5)
    assert phases.worker == "unknown"
    assert not phases.complete
    assert "WARNING phase accounting" in testphases.line(phases)


@pytest.mark.parametrize("duration,waited", [(float("nan"), 0), (-1, 0), (1, float("inf")), (1, -2)])
def test_invalid_worker_measurements_remain_visible_without_poisoning_totals(tmp_path, monkeypatch, duration, waited):
    rows = [("setup", "gw0", duration, waited), ("call", "gw0", 1, None), ("teardown", "gw0", 1, None)]
    phases = recorded(tmp_path, monkeypatch, rows, wall=10)
    assert not phases.complete
    assert phases.workers["gw0"]["invalid"]
    assert phases.fixed == 9
    assert "WARNING phase accounting" in testphases.line(phases)
    assert "WARNING fixed overhead" in testphases.line(phases)


@pytest.mark.parametrize("field,value", [("wall", float("nan")), ("tests", -1), ("queued", float("inf"))])
def test_invalid_phase_measurements_warn(field, value):
    values = {"wall": 10, "collection": 1, "setup": 1, "tests": 1, "teardown": 1, "queued": 0}
    values[field] = value
    assert "WARNING phase accounting" in testphases.line(Phases(**values))

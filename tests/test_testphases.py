"""The phase line and its warning thresholds."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import testphases
from testphases import Phases


def test_a_run_with_small_overhead_prints_no_warning():
    text = testphases.line(Phases(wall=12.0, collection=1.0, setup=0.5, tests=10.0, teardown=0.5))
    assert "collection 1.0 s, session setup 0.5 s, tests 10.0 s, teardown 0.5 s" in text
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


def test_the_recorder_averages_the_permit_wait_the_workers_report(tmp_path):
    recorder = testphases.Recorder(str(tmp_path / "phases.json"))
    for worker, waited in (("gw0", 6.0), ("gw1", 2.0)):
        recorder.pytest_runtest_logreport(report("setup", worker, waited))
        recorder.pytest_runtest_logreport(report("call", worker, duration=1.0))
        recorder.pytest_runtest_logreport(report("teardown", worker))
    recorder.pytest_runtest_logreport(report("setup", "gw0", 4.0))
    recorder.pytest_sessionfinish(None)
    assert testphases.read(tmp_path / "phases.json").queued == 6.0


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

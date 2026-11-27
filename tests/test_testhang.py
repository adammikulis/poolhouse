"""scripts/testhang.py: a test its own timeout does not end is named and ends within a bound. Each case is a real pytest run under the
broker, in a slots folder of its own, over a test written to hang the way `pytest-timeout` cannot stop."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "testslots.py"
BOUND_S = 60.0
"""How long a run over a hanging test may take here before it counts as unbounded: its limit is 3 s."""
pytestmark = pytest.mark.slow

SWALLOWS = """
import subprocess, sys, time
from pathlib import Path
import pytest

@pytest.mark.timeout(1)
def test_swallows_its_timeout():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    Path(__file__).with_name("child.pid").write_text(str(child.pid))
    while True:
        try:
            time.sleep(600)
        except BaseException:
            pass

def test_after_it():
    Path(__file__).with_name("after").write_text("ran")
"""

TEARDOWN = """
import time
import pytest

@pytest.fixture
def stuck():
    yield
    time.sleep(600)

@pytest.mark.timeout(1)
def test_fails_and_then_hangs_in_teardown(stuck):
    time.sleep(600)
"""

QUICK = """
import time
import pytest

@pytest.mark.timeout(1)
def test_ends_by_its_own_timeout():
    time.sleep(600)

def test_passes():
    time.sleep(2.5)
"""


def run(tmp_path: Path, body: str, workers: str, still: str = "60") -> tuple[subprocess.CompletedProcess, float]:
    """Run ``body`` through the broker with ``workers`` as xdist's count; the finished run and how long it took."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "test_hangs.py").write_text(body)
    (project / "pytest.ini").write_text("[pytest]\ntimeout = 30\n")
    inherited = {name: value for name, value in os.environ.items()
                 if name not in {"DEV_TEST_REMOTE_LEASE", "DEV_TEST_LEASE"} and not name.startswith("PYTEST_XDIST")}
    environment = {**inherited, "DEV_TEST_SLOTS_DIR": str(tmp_path / "slots"), "DEV_TEST_BUDGET": "2", "DEV_TEST_HANG_GRACE_S": "2",
                   "DEV_TEST_STILL_S": still, "PYTHONPATH": str(ROOT / "scripts")}
    count = ["-n", workers] if workers == "0" else ["-n", "{workers}"]
    began = time.monotonic()
    process = subprocess.Popen([sys.executable, str(SCRIPT), "pytest", "--want", "1", "--", sys.executable, "-m", "pytest", "-q",
                                *count, "-p", "testslots_pytest", "-c", str(project / "pytest.ini"), str(project / "test_hangs.py")],
                               cwd=project, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        output, _ = process.communicate(timeout=BOUND_S)
    except subprocess.TimeoutExpired:
        for each in [*psutil.Process(process.pid).children(recursive=True), psutil.Process(process.pid)]:
            each.kill()
        output, _ = process.communicate(timeout=30)
        pytest.fail(f"the run did not end within {BOUND_S:.0f} s:\n{output}")
    return subprocess.CompletedProcess(process.args, process.returncode, output), time.monotonic() - began


def gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def test_a_test_that_swallows_its_timeout_is_named_failed_and_the_run_goes_on(tmp_path):
    done, took = run(tmp_path, SWALLOWS, "1")
    assert done.returncode != 0, done.stdout
    assert "test: HUNG: " in done.stdout and "test_hangs.py::test_swallows_its_timeout had not ended 3 s after it began" in done.stdout, done.stdout
    assert "crashed while running" in done.stdout, done.stdout
    assert 'test_hangs.py", line' in done.stdout, "the stack shows where it is held"
    assert (tmp_path / "project" / "after").exists(), f"the next test still runs:\n{done.stdout}"
    child = int((tmp_path / "project" / "child.pid").read_text())
    until = time.monotonic() + 10
    while not gone(child) and time.monotonic() < until:
        time.sleep(0.05)
    assert gone(child), "the process the hung test started is ended with it"
    assert took < BOUND_S


@pytest.mark.parametrize("workers", ["0", "1"])
def test_a_teardown_that_hangs_after_the_timeout_ends_the_run_naming_the_test(tmp_path, workers):
    done, _ = run(tmp_path, TEARDOWN, workers)
    assert done.returncode != 0, done.stdout
    assert "test: HUNG: " in done.stdout and "test_fails_and_then_hangs_in_teardown" in done.stdout, done.stdout


def test_a_test_its_own_timeout_ends_and_a_test_within_its_limit_are_left_alone(tmp_path):
    done, _ = run(tmp_path, QUICK, "1")
    assert done.returncode == 1, done.stdout
    assert "HUNG" not in done.stdout and "1 failed, 1 passed" in done.stdout, done.stdout


SLOW = """
import time
import pytest

def test_a_slow_one():
    time.sleep(2.5)

@pytest.mark.timeout(1)
def test_one_its_timeout_fails():
    time.sleep(60)

def test_the_last():
    time.sleep(2.5)

def test_that_only_speaks_of_a_timeout():
    assert False, "Failed: Timeout (>1.0s) from pytest-timeout."
"""


@pytest.mark.parametrize("workers", ["0", "1"])
def test_a_run_names_its_slow_tests_while_they_run_and_a_timed_out_one_as_it_fails(tmp_path, workers):
    done, _ = run(tmp_path, SLOW, workers, still="1")
    assert done.returncode == 1, done.stdout
    first = done.stdout.index("test: still running after")
    failed = done.stdout.index("test: TIMED OUT in call: ")
    last = done.stdout.rindex("test: still running after")
    assert first < failed < last < done.stdout.index("short test summary info"), done.stdout
    assert "test_a_slow_one" in done.stdout[first:failed] and "test_one_its_timeout_fails" in done.stdout[failed:last].splitlines()[0]
    assert "test_the_last" in done.stdout[last:].splitlines()[0] and "HUNG" not in done.stdout
    assert done.stdout.count("test: TIMED OUT") == 1, "a failure that only quotes the words is no timeout"

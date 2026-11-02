"""scripts/testslots_pytest.py: the wait for a CPU permit is outside every test timer and has its own outcome."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from defusedxml import ElementTree

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "testslots.py"
NODE = "test_queued.py::test_after_the_wait"
TIMER = 1.0
"""The timeout marker of the generated test, in seconds."""

pytestmark = pytest.mark.slow

HOLDER = """
import importlib.util, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("testslots", sys.argv[1]); m = importlib.util.module_from_spec(spec)
sys.modules["testslots"] = m; spec.loader.exec_module(m)
with m.lease(1, 1, label=sys.argv[2], say=lambda s: None):
    while not Path(sys.argv[3]).exists():
        time.sleep(0.01)
"""

CONFTEST = """
import os, time
from pathlib import Path
import pytest

@pytest.hookimpl(tryfirst=True)
def pytest_collection_finish(session):
    marks = Path(os.environ["HANDSHAKE"])
    (marks / "collecting").touch()
    deadline = time.monotonic() + 60
    while not (marks / "go").exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
"""

PASSES = f"""
import os, pytest
from pathlib import Path

@pytest.mark.timeout({TIMER})
def test_after_the_wait():
    Path(os.environ["RAN"]).write_text("ran")
"""

RUNS_PAST_ITS_TIMER = f"""
import os, pytest, time
from pathlib import Path

@pytest.mark.timeout({TIMER})
def test_after_the_wait():
    Path(os.environ["RAN"]).write_text("ran")
    time.sleep({TIMER * 4})
"""


def _environment(tmp_path: Path, wait_s: float) -> dict[str, str]:
    inherited = {name: value for name, value in os.environ.items() if name not in {"DEV_TEST_REMOTE_LEASE", "DEV_TEST_LEASE"}}
    (tmp_path / "handshake").mkdir(parents=True, exist_ok=True)
    return {**inherited, "DEV_TEST_SLOTS_DIR": str(tmp_path / "slots"), "DEV_TEST_BUDGET": "2",
            "DEV_TEST_WAIT_S": str(wait_s), "PYTHONPATH": str(ROOT / "scripts"), "HANDSHAKE": str(tmp_path / "handshake"),
            "RAN": str(tmp_path / "ran")}


def _until(condition, what: str, seconds: float = 60) -> None:
    end = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < end, what
        time.sleep(0.02)


def _slots(tmp_path: Path):
    sys.path.insert(0, str(ROOT / "scripts"))
    import testslots
    (tmp_path / "slots").mkdir(exist_ok=True)
    with testslots._mutex(tmp_path / "slots"):
        return [(slot.data["label"], slot.granted) for slot in testslots._read(tmp_path / "slots")]


def _holder(tmp_path: Path, environment: dict[str, str], label: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", HOLDER, str(SCRIPT), label, str(tmp_path / "release")], env=environment)


def queued_run(tmp_path: Path, body: str, *, wait_s: float, hold_s: float | None) -> tuple[subprocess.CompletedProcess, Path]:
    """Run a pytest file under the broker while two other processes hold every permit when the test asks for its own.

    The test's permit waits ``hold_s`` seconds, or until the wait limit when that is None, and the holders then leave."""
    project = tmp_path / "project"
    project.mkdir(parents=True)
    (project / "conftest.py").write_text(CONFTEST)
    (project / "test_queued.py").write_text(body)
    (project / "pytest.ini").write_text("[pytest]\n")
    environment, junit = _environment(tmp_path, wait_s), tmp_path / "junit.xml"
    release = tmp_path / "release"
    first = _holder(tmp_path, environment, "first holder")
    _until(lambda: ("first holder", 1) in _slots(tmp_path), "the first holder never got its permit")
    run = subprocess.Popen([sys.executable, str(SCRIPT), "pytest", "--want", "1", "--", sys.executable, "-m", "pytest", "-q",
                            "-n", "{workers}", "-p", "testslots_pytest", "-c", str(project / "pytest.ini"),
                            f"--junitxml={junit}", str(project / "test_queued.py")],
                           env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    second = None
    try:
        _until(lambda: (tmp_path / "handshake" / "collecting").exists(), "the run never finished collecting")
        second = _holder(tmp_path, environment, "second holder")
        _until(lambda: ("second holder", 0) in _slots(tmp_path), "the second holder never queued")
        (tmp_path / "handshake" / "go").touch()
        _until(lambda: (NODE, 0) in _slots(tmp_path), "the test never queued for its permit")
        if hold_s is not None:
            time.sleep(hold_s)
        output, _ = run.communicate(timeout=120) if hold_s is None else (None, None)
    finally:
        release.touch()
        for holder in (first, second):
            if holder is not None:
                holder.wait(timeout=60)
    if output is None:
        output, _ = run.communicate(timeout=120)
    return subprocess.CompletedProcess(run.args, run.returncode, output), junit


def _case(junit: Path):
    return ElementTree.parse(junit).getroot().find(".//testcase")


def _waited(case) -> float:
    return float(next(p.get("value") for p in case.iter("property") if p.get("name") == "admission_wait"))


def test_a_permit_wait_is_not_counted_against_the_test_timeout(tmp_path):
    result, junit = queued_run(tmp_path, PASSES, wait_s=60, hold_s=TIMER * 3)
    case = _case(junit)
    assert result.returncode == 0, result.stdout
    assert case.find("failure") is None and case.find("error") is None and case.find("skipped") is None, result.stdout
    assert (tmp_path / "ran").read_text() == "ran"
    assert _waited(case) > TIMER * 2, "the wait the test sat through is recorded with the test"


def test_a_wait_past_the_limit_is_not_started_with_status_75_and_not_a_failure(tmp_path):
    result, junit = queued_run(tmp_path, PASSES, wait_s=8, hold_s=None)
    assert result.returncode == 75, result.stdout
    assert "NOT STARTED, machine overloaded: waited" in result.stdout, result.stdout
    assert NODE in result.stdout
    assert not (tmp_path / "ran").exists(), "a test that never got its permit does not run"
    case = _case(junit)
    assert case.find("failure") is None and case.find("error") is None, result.stdout


def test_the_report_names_the_limit_that_fired(tmp_path):
    waited, _ = queued_run(tmp_path / "waited", RUNS_PAST_ITS_TIMER, wait_s=60, hold_s=TIMER * 3)
    assert waited.returncode == 1, waited.stdout
    assert "test timed out after" in waited.stdout and "running" in waited.stdout, waited.stdout
    assert "s for a permit" in waited.stdout and "DEV_TEST_WAIT_S" not in waited.stdout, waited.stdout
    assert "NOT STARTED" not in waited.stdout
    refused, _ = queued_run(tmp_path / "refused", PASSES, wait_s=8, hold_s=None)
    assert "NOT STARTED, machine overloaded: waited" in refused.stdout and "(DEV_TEST_WAIT_S" in refused.stdout, refused.stdout
    assert "timed out" not in refused.stdout and "Timeout" not in refused.stdout, refused.stdout

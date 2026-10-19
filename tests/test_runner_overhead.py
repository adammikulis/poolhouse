"""The fixed cost of a scoped run, measured as CPU seconds, which a busy machine does not stretch the way it stretches wall time."""

from __future__ import annotations

import os
import resource
import subprocess
import sys
from pathlib import Path

import testcommand

ROOT = Path(__file__).resolve().parent.parent
RUNNER_START_CPU_S = 3.0
"""The runner is started twice for one run, once to follow the job and once as the job."""
PYTEST_START_CPU_S = 3.0
"""One pytest process loads the plugins and `tests/conftest.py`, and a parallel run starts one per worker."""


def cpu_of(command: list[str], **environment: str) -> tuple[float, subprocess.CompletedProcess]:
    """The user and system CPU seconds the command and its children used."""
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    done = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False, env={**os.environ, **environment})
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    return (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime), done


def test_the_runner_starts_within_its_cpu_budget():
    used, done = cpu_of([sys.executable, str(ROOT / "scripts" / "test"), "--help"])
    assert done.returncode == 0, done.stderr
    assert used < RUNNER_START_CPU_S, f"scripts/test took {used:.1f} CPU s to start; the budget is {RUNNER_START_CPU_S} s"


def test_a_pytest_process_collecting_one_file_stays_within_its_cpu_budget():
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-n", "0", "-p", "no:cacheprovider",
               *testcommand.UNUSED_PLUGINS, "tests/test_testphases.py"]
    used, done = cpu_of(command, PYTHONPATH=os.pathsep.join((str(ROOT / "src"), str(ROOT / "scripts"))))
    assert done.returncode == 0, done.stdout + done.stderr
    assert used < PYTEST_START_CPU_S, f"one pytest process took {used:.1f} CPU s to start and collect; the budget is {PYTEST_START_CPU_S} s"

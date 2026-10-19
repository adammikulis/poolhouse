"""A test that stalls fails at a fixed limit and is named, instead of holding a whole run for minutes."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import envcheck  # noqa: E402

LIMIT_S = 120
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))


def ini() -> dict:
    return envcheck.pyproject(REPO)["tool"]["pytest"]["ini_options"]


def test_every_test_has_a_limit_of_at_most_two_minutes() -> None:
    assert 0 < int(ini()["timeout"]) <= LIMIT_S


def test_the_test_extra_installs_the_plugin_that_enforces_it() -> None:
    names = {re.split(r"[<>=\[; ]", requirement)[0] for requirement in envcheck.extra_requirements("test", REPO)}
    assert "pytest-timeout" in names


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_a_workflow_that_installs_pytest_for_the_repository_config_installs_the_plugin(workflow: Path) -> None:
    for line in workflow.read_text(encoding="utf-8").splitlines():
        if "pip install" in line and re.search(r"\bpytest\b(?!-)", line) and "pytest-timeout" not in line:
            raise AssertionError(f"{workflow.name} installs pytest without pytest-timeout: {line.strip()}")


def test_a_stalled_test_fails_under_the_repository_config_and_is_named(tmp_path: Path) -> None:
    stalled = tmp_path / "test_stalled.py"
    stalled.write_text("import time\n\ndef test_it_waits_for_ever():\n    time.sleep(60)\n", encoding="utf-8")
    run = subprocess.run([sys.executable, "-m", "pytest", "-c", str(REPO / "pyproject.toml"), "--rootdir", str(tmp_path),
                          "-o", "timeout=1", "-p", "no:cacheprovider", str(stalled)],
                         cwd=tmp_path, capture_output=True, text=True, timeout=45, check=False)
    assert run.returncode == 1, run.stdout + run.stderr
    assert "Timeout" in run.stdout and "test_it_waits_for_ever" in run.stdout

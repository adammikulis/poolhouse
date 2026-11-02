"""An agent never runs the whole suite locally, however it selects it; any list of files short of the whole suite is allowed."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import testjobs  # noqa: E402

LINE = "the whole suite runs on a schedule and on pull requests; name the test files your change affects"
MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE")
RUNNER = "import time\ntime.sleep(60)\n"
GIT = ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid"]


def named(count: int) -> list[str]:
    return [f"tests/test_{number:02d}.py" for number in range(count)]


@pytest.fixture
def scratch(tmp_path):
    """A scratch checkout with a copy of the scripts and forty one-test files: a run that slips through runs nothing real."""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(ROOT / "pyproject.toml", repo / "pyproject.toml")
    shutil.copytree(SCRIPTS, repo / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (repo / "src").symlink_to(ROOT / "src")
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text("from testoptions import pytest_addoption  # noqa: F401\n")
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\nsrc\n")
    for name in named(40):
        (repo / name).write_text("def test_one():\n    pass\n")
    subprocess.run([*GIT, "init", "-q", "-b", "dev"], cwd=repo, check=True)
    subprocess.run([*GIT, "add", "scripts", "tests", "pyproject.toml", ".gitignore"], cwd=repo, check=True)
    subprocess.run([*GIT, "commit", "-q", "-m", "scratch"], cwd=repo, check=True)
    return repo


def environment(tmp_path: Path, marker: str = "") -> dict[str, str]:
    base = {name: value for name, value in os.environ.items() if name not in MARKERS and name != "DEV_TEST_JOB"}
    base.update(DEV_TEST_SLOTS_DIR=str(tmp_path / "slots"), DEV_TEST_REUSE_DIR=str(tmp_path / "reuse"),
                HOME=str(tmp_path / "home"))
    return {**base, marker: "1"} if marker else base


def scripts_test(tmp_path: Path, repo: Path, marker: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(repo / "scripts" / "test"), *arguments], cwd=repo,
                          env=environment(tmp_path, marker), capture_output=True, text=True, timeout=300)


def queued(tmp_path: Path) -> list[Path]:
    """Job records and run records the broker or the job table wrote under the scratch state."""
    return [path for path in tmp_path.rglob("*") if path.is_file() and (path.name == "spec.json" or path.suffix == ".run")]


@pytest.mark.parametrize(("marker", "arguments"), [
    ("CLAUDECODE", ("full",)), ("CLAUDECODE", ("slow",)), ("CLAUDECODE", ("fast",)), ("CLAUDECODE", ("all",)),
    ("CLAUDECODE", ("submit", "all")), ("POOLHOUSE_AGENT", ("full",)), ("POOLHOUSE_NONINTERACTIVE", ("all",)),
    ("CODEX_THREAD_ID", ("full",)), ("CLAUDECODE", ("full", "--on", "all"))])
def test_an_agent_whole_tier_run_is_refused_before_it_queues(tmp_path, scratch, marker, arguments):
    result = scripts_test(tmp_path, scratch, marker, *arguments)
    assert result.returncode == 4
    assert LINE in result.stderr
    assert queued(tmp_path) == []


def test_an_agent_run_naming_every_test_file_is_refused_before_it_queues(tmp_path, scratch):
    result = scripts_test(tmp_path, scratch, "CLAUDECODE", "all", *named(40))
    assert result.returncode == 4
    assert LINE in result.stderr
    assert queued(tmp_path) == []


def test_a_directory_counts_as_the_files_in_it(tmp_path, scratch):
    result = scripts_test(tmp_path, scratch, "POOLHOUSE_AGENT", "all", "tests")
    assert result.returncode == 4
    assert LINE in result.stderr


def test_an_agent_run_of_thirty_nine_files_passes_the_size_check_and_a_person_run_of_any_size_starts(tmp_path, scratch):
    agent = scripts_test(tmp_path, scratch, "CLAUDECODE", "all", *named(39), "-n", "1")
    person = scripts_test(tmp_path, scratch, "", "all", *named(40), "-n", "1")
    assert LINE not in agent.stderr + agent.stdout + person.stderr + person.stdout
    assert agent.returncode == 0, agent.stdout + agent.stderr
    assert person.returncode == 0, person.stdout + person.stderr


@pytest.fixture
def table(tmp_path, scratch, monkeypatch):
    for name in MARKERS:
        monkeypatch.delenv(name, raising=False)
    script = tmp_path / "runner.py"
    script.write_text(RUNNER)
    return testjobs.Jobs(tmp_path / "store"), script, scratch


def submit(table, argv):
    jobs, script, root = table
    job = jobs.submit(argv, {"id": "worker"}, root, script, {})
    jobs.cancel(job, "worker")
    return job


def test_the_job_table_takes_any_list_of_files_short_of_the_suite_from_an_agent(table, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert submit(table, ["all", *named(31)])
    assert submit(table, ["all", *named(39)])


@pytest.mark.parametrize("argv", [["all", *named(40)], ["full"], ["all"], ["slow", "tests"], ["fast"]])
def test_the_job_table_never_admits_an_agent_whole_run(table, monkeypatch, argv):
    monkeypatch.setenv("POOLHOUSE_NONINTERACTIVE", "1")
    with pytest.raises(testjobs.Refused, match="whole suite runs on a schedule"):
        submit(table, argv)
    assert table[0].ids() == []


@pytest.mark.parametrize("argv", [["all", *named(40)], ["full"], ["gate"], ["quick"]])
def test_a_person_run_is_never_refused(table, argv):
    assert submit(table, argv)


@pytest.mark.parametrize("tier", ["quick", "gate"])
def test_quick_and_gate_stay_allowed_for_an_agent(table, monkeypatch, tier):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert submit(table, [tier])


def broker_pytest(tmp_path, repo, marker, *files):
    return subprocess.run([sys.executable, str(repo / "scripts" / "testslots.py"), "pytest", "--", sys.executable, "-m", "pytest", "-q", *files],
                          cwd=repo, env={**environment(tmp_path, marker), "PYTHONPATH": str(repo / "scripts")},
                          capture_output=True, text=True, timeout=300)


def test_the_broker_never_admits_every_test_file_from_an_agent(tmp_path, scratch):
    result = broker_pytest(tmp_path, scratch, "CLAUDECODE", *named(40))
    assert result.returncode == 4
    assert LINE in result.stderr
    assert queued(tmp_path) == []


def test_the_broker_admits_thirty_nine_files_from_an_agent_and_every_file_from_a_person(tmp_path, scratch):
    assert broker_pytest(tmp_path, scratch, "CLAUDECODE", *named(39)).returncode == 0
    assert broker_pytest(tmp_path, scratch, "", *named(40)).returncode == 0

"""The fixed cost of a `scripts/test` run stays small: a small run starts few workers, the hook check
spawns nothing when the hooks are in place, and argument parsing imports no conftest."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import githooks_ready
import pytest
import testoptions
import testslots
import testslots_runner

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(("estimate", "ceiling", "workers"), [
    (None, 15, 15),
    (0.0, 15, 1),
    (3.9, 15, 1),
    (4.0, 15, 2),
    (30.0, 15, 5),
    (100.0, 15, 10),
    (10_000.0, 15, 15),
    (10_000.0, 1, 1),
])
def test_the_pool_grows_with_the_square_root_of_the_work(estimate, ceiling, workers):
    assert testslots.pool_for(estimate, ceiling) == workers


@pytest.fixture(autouse=True)
def _slots_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_TEST_SLOTS_DIR", str(tmp_path / "slots"))


def prepared(environment: dict[str, str], want: int = 0) -> tuple[list[str], dict[str, str]]:
    admission = SimpleNamespace(endpoint="127.0.0.1:1", token="t")
    command, env = testslots_runner.prepare_pytest(["pytest", "-n", "{workers}"], environment, admission, None,
                                                   testslots_runner.Preparation(want=want))
    return command, env


def test_a_run_with_little_work_starts_one_worker():
    command, env = prepared({"DEV_TEST_BUDGET": "15", "DEV_TEST_ESTIMATE_S": "0.0"})
    assert command == ["pytest", "-n", "1"]
    assert env["DEV_TEST_WORKERS"] == "1"


def test_a_run_with_much_work_starts_the_whole_pool():
    command, env = prepared({"DEV_TEST_BUDGET": "15", "DEV_TEST_ESTIMATE_S": "5000"})
    assert command == ["pytest", "-n", "15"]
    assert env["DEV_TEST_WORKERS"] == "15"


def test_a_run_without_an_estimate_starts_the_whole_pool():
    command, _ = prepared({"DEV_TEST_BUDGET": "15"})
    assert command == ["pytest", "-n", "15"]


def test_an_explicit_worker_count_is_kept():
    command, _ = prepared({"DEV_TEST_BUDGET": "15", "DEV_TEST_ESTIMATE_S": "0.0"}, want=6)
    assert command == ["pytest", "-n", "6"]


def git_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repository whose installer leaves ``ran`` behind when it is run."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "scripts").mkdir()
    installer = tmp_path / "scripts" / "install-hooks.sh"
    installer.write_text('#!/bin/sh\ntouch "$(dirname "$0")/ran"\n')
    hooks = tmp_path / ".git" / "hooks"
    return hooks, tmp_path / "scripts" / "ran"


def link_ours(hooks: Path, name: str) -> None:
    (hooks / name).unlink(missing_ok=True)
    (hooks / name).symlink_to(f"../../scripts/hooks/{name}")


def test_hooks_that_are_ours_or_local_wrappers_run_no_installer(tmp_path):
    hooks, ran = git_repo(tmp_path)
    (hooks / "pre-commit").write_text("#!/bin/sh\nexec true\n")
    (hooks / "commit-msg").write_text("#!/bin/sh\nexec true\n")
    link_ours(hooks, "pre-push")
    githooks_ready.ensure(tmp_path)
    assert not ran.exists()


def test_a_missing_hook_runs_the_installer(tmp_path):
    hooks, ran = git_repo(tmp_path)
    link_ours(hooks, "pre-commit")
    link_ours(hooks, "commit-msg")
    githooks_ready.ensure(tmp_path)
    assert ran.exists()


def test_a_symlink_to_another_script_runs_the_installer(tmp_path):
    hooks, ran = git_repo(tmp_path)
    for name in githooks_ready.HOOKS:
        (hooks / name).symlink_to("/bin/true")
    githooks_ready.ensure(tmp_path)
    assert ran.exists()


def test_the_conftest_adds_the_options_scripts_test_parses_with(request):
    plugins = request.config.pluginmanager.get_plugins()
    conftest = next(p for p in plugins if str(getattr(p, "__file__", "")).endswith("tests/conftest.py"))
    assert conftest.pytest_addoption is testoptions.pytest_addoption


def test_parsing_arguments_imports_no_conftest_and_keeps_the_tier_options():
    code = ("import sys, testclass, testselectors\n"
            "from pathlib import Path\n"
            "chosen = testclass.selectors(['--slow', '--gate', '--redteam', '-m', 'not slow', '-n', '3',\n"
            "                              'tests/test_test_fixed_overhead.py::test_x', 'tests/test_testphases.py'])\n"
            "testselectors.validate(['--slow', 'tests/test_testphases.py'], Path.cwd())\n"
            "print(chosen)\n"
            "print([name for name in sys.modules if name.endswith('conftest')])\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PYTHONPATH": f"{ROOT / 'scripts'}:{ROOT / 'src'}"})
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-2:] == [
        "['tests/test_test_fixed_overhead.py::test_x', 'tests/test_testphases.py']", "[]"]


def test_a_request_waits_longer_the_further_back_it_is_in_the_queue():
    assert testslots.retry_after(0) == 0.01
    assert testslots.retry_after(10) == pytest.approx(0.05)
    assert testslots.retry_after(10_000) == 0.5


WAITER = '''
import sys, time
sys.path.insert(0, sys.argv[1])
import testslots as m
ahead, calls, real, began = int(sys.argv[2]), [], m._decide, time.monotonic()

def decide(me, path, slots, cap):
    calls.append(1)
    if time.monotonic() - began < 1.0:
        return 0, 0, ["other"] * ahead
    return real(me, path, slots, cap)

m._decide = decide
with m.lease(1, 1, label="waiter", say=lambda text: None):
    pass
print(len(calls))
'''


def decisions_while_waiting_a_second(tmp_path, ahead: int) -> int:
    environment = {name: value for name, value in os.environ.items()
                   if name not in {"DEV_TEST_REMOTE_LEASE", "DEV_TEST_LEASE", "DEV_TEST_CLASS", "DEV_TEST_ESTIMATE_S"}}
    environment.update(DEV_TEST_SLOTS_DIR=str(tmp_path / "slots"), DEV_TEST_BUDGET="4", DEV_TEST_WAIT_S="60")
    done = subprocess.run([sys.executable, "-c", WAITER, str(ROOT / "scripts"), str(ahead)], env=environment,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return int(done.stdout.split()[-1])


def test_a_request_far_back_in_the_queue_does_not_read_every_record_a_hundred_times_a_second(tmp_path):
    assert decisions_while_waiting_a_second(tmp_path, ahead=100) <= 6


def test_the_request_at_the_head_of_the_queue_keeps_deciding_often(tmp_path):
    assert decisions_while_waiting_a_second(tmp_path, ahead=0) >= 10

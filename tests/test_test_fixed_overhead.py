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
import testclass
import testoptions
import testselectors
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


def test_a_pool_of_one_runs_pytest_itself_unless_a_worker_was_asked_for():
    command, env = prepared({"DEV_TEST_BUDGET": "15", "DEV_TEST_ESTIMATE_S": "0.0"})
    assert command == ["pytest", "-n", "0"]
    assert env["DEV_TEST_WORKERS"] == "1"
    command, env = prepared({"DEV_TEST_BUDGET": "15", "DEV_TEST_ESTIMATE_S": "0.0"}, want=1)
    assert command == ["pytest", "-n", "1"]
    assert env["DEV_TEST_WORKERS"] == "1"
    command, env = prepared({"DEV_TEST_BUDGET": "15", "DEV_TEST_ESTIMATE_S": "4.0"})
    assert command == ["pytest", "-n", "2"]
    assert env["DEV_TEST_WORKERS"] == "2"


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


def test_validation_and_classification_of_the_same_arguments_parse_them_once(monkeypatch):
    parsed = []
    real = testoptions.parse_arguments
    monkeypatch.setattr(testoptions, "parse_arguments", lambda arguments: parsed.append(arguments) or real(arguments))
    testoptions._positional.cache_clear()
    arguments = ["--slow", "-n", "2", "tests/test_testphases.py"]
    try:
        testselectors.validate(arguments, ROOT)
        assert testclass.selectors(arguments) == ["tests/test_testphases.py"]
        assert len(parsed) == 1
        assert testoptions.positional(arguments) == ["tests/test_testphases.py"]
        assert len(parsed) == 1
    finally:
        testoptions._positional.cache_clear()


WHERE = (
    "import os\nfrom pathlib import Path\n\n\n"
    "def test_where():\n"
    "    Path(os.environ['WHERE_LOG']).write_text(os.environ.get('PYTEST_XDIST_WORKER', 'no xdist worker'))\n"
)


def where_a_test_ran(tmp_path, want: str, estimate: str) -> str:
    """The xdist worker (or none) that ran a test through the supervisor with ``want`` workers asked for."""
    tmp_path.mkdir()
    (tmp_path / "test_where.py").write_text(WHERE)
    environment = {name: value for name, value in os.environ.items()
                   if name not in {"DEV_TEST_REMOTE_LEASE", "DEV_TEST_LEASE", "DEV_TEST_CLASS", "DEV_TEST_ESTIMATE_S"}}
    environment.update(DEV_TEST_SLOTS_DIR=str(tmp_path / "slots"), DEV_TEST_BUDGET="4", DEV_TEST_WAIT_S="60",
                       DEV_TEST_ESTIMATE_S=estimate, PYTHONPATH=str(ROOT / "scripts"), WHERE_LOG=str(tmp_path / "where.log"))
    command = [sys.executable, str(ROOT / "scripts" / "testslots.py"), "pytest", "--want", want, "--", sys.executable, "-m", "pytest",
               "-q", "-n", "{workers}", "-p", "testslots_pytest", "-p", "no:cacheprovider", str(tmp_path / "test_where.py")]
    done = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    return (tmp_path / "where.log").read_text()


def test_a_small_run_through_the_supervisor_starts_no_xdist_worker_and_a_larger_one_starts_them(tmp_path):
    assert where_a_test_ran(tmp_path / "small", "auto", "0.5") == "no xdist worker"
    assert where_a_test_ran(tmp_path / "asked", "1", "0.5") == "gw0"
    assert where_a_test_ran(tmp_path / "large", "auto", "40") in {"gw0", "gw1", "gw2", "gw3"}


LOADED = (
    "import os, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "import testslots\n"
    "testslots._cores = lambda: 16\n"
    "os.getloadavg = lambda: (500.0, 500.0, 500.0)\n"
    "with testslots.lease(int(sys.argv[2]), 1, label='wide', say=lambda text: None) as got:\n"
    "    print(got.workers)\n"
)


def test_a_request_is_granted_in_full_on_a_machine_under_heavy_load(tmp_path):
    environment = {name: value for name, value in os.environ.items()
                   if name not in {"DEV_TEST_REMOTE_LEASE", "DEV_TEST_LEASE", "DEV_TEST_CLASS", "DEV_TEST_ESTIMATE_S", "DEV_TEST_BUDGET"}}
    environment.update(DEV_TEST_SLOTS_DIR=str(tmp_path / "slots"), DEV_TEST_RESERVED_CORES="1", DEV_TEST_WAIT_S="60")
    done = subprocess.run([sys.executable, "-c", LOADED, str(ROOT / "scripts"), "12"], env=environment,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert int(done.stdout.split()[-1]) == 12

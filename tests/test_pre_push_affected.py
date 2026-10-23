"""The push check runs the tests a push can break, in real git repositories, and refuses the push when one fails or none can be chosen."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_pre_push import commit, git, push

REPO = Path(__file__).resolve().parent.parent
RUNNER = '''#!/usr/bin/env python3
import json, os, subprocess, sys
with open(os.environ["STUB_LOG"], "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "git_dir": os.environ.get("GIT_DIR", "")}) + "\\n")
env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", *[a for a in sys.argv[2:] if a != "--redteam"]],
                         env={**env, "PYTHONPATH": "src"}))
'''
EXITS_4 = "#!/usr/bin/env python3\nimport sys\nsys.exit(4)\n"
UNTOUCHED = "from poolhouse import mod\n\n\ndef test_value():\n    assert mod.VALUE == 1\n"
OTHER = "from poolhouse import other\n\n\ndef test_name():\n    assert other.NAME == 'o'\n"
SKIPPED = "def test_other():\n    assert True\n"


@pytest.fixture
def repo(tmp_path):
    """A checkout on `0.9dev` whose origin already holds its first commit, with a module and a test that imports it."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "0.9dev", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "0.9dev", str(work)], check=True)
    git(work, "remote", "add", "origin", str(origin))
    for name, text in {"README.md": "x\n", "src/poolhouse/__init__.py": "", "src/poolhouse/mod.py": "VALUE = 1\n",
                       "src/poolhouse/other.py": "NAME = 'o'\n", "tests/test_untouched.py": UNTOUCHED, "tests/test_other.py": OTHER,
                       "tests/test_unrelated.py": SKIPPED, "scripts/test": RUNNER}.items():
        commit(work, name, text)
    git(work, "push", "-q", "origin", "0.9dev")
    return work


def landing(work: Path, log: Path, **env: str) -> subprocess.CompletedProcess:
    """The agent's push of HEAD over the commit origin holds."""
    old = git(work, "rev-parse", "origin/0.9dev")
    return push(work, "0.9dev", base=old, CLAUDECODE="1", STUB_LOG=str(log), **env)


def calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_a_push_that_breaks_a_test_it_did_not_touch_is_refused(repo, tmp_path):
    commit(repo, "src/poolhouse/mod.py", "VALUE = 2\n")
    done = landing(repo, tmp_path / "log")
    assert done.returncode != 0
    assert "FAILED tests/test_untouched.py::test_value" in done.stderr
    assert "run it again with: scripts/test all tests/test_untouched.py --redteam" in done.stderr
    assert "the push breaks the tests above" in done.stderr


def test_a_test_that_was_already_red_before_the_push_is_named_and_not_held_against_it(repo, tmp_path):
    commit(repo, "tests/test_red.py", "from poolhouse import mod\n\n\ndef test_was_red():\n    assert mod.VALUE == 99\n")
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 9\n")
    done = landing(repo, tmp_path / "log")
    assert done.returncode == 0, done.stderr
    assert "already red before this push" in done.stderr and "FAILED tests/test_red.py::test_was_red" in done.stderr
    assert "refused" not in done.stderr


def test_an_old_red_test_does_not_hide_a_test_the_push_breaks(repo, tmp_path):
    commit(repo, "tests/test_red.py", "from poolhouse import mod\n\n\ndef test_was_red():\n    assert mod.VALUE == 99\n")
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "src/poolhouse/mod.py", "VALUE = 2\n")
    done = landing(repo, tmp_path / "log")
    assert done.returncode != 0
    assert "run it again with: scripts/test all tests/test_untouched.py --redteam" in done.stderr
    assert "tests/test_red.py" not in done.stderr.split("run it again with:")[1]


def test_a_new_test_that_fails_is_a_failure_of_the_push(repo, tmp_path):
    commit(repo, "tests/test_fresh.py", "from poolhouse import mod\n\n\ndef test_fresh():\n    assert mod.VALUE == 99\n")
    done = landing(repo, tmp_path / "log")
    assert done.returncode != 0
    assert "run it again with: scripts/test all tests/test_fresh.py --redteam" in done.stderr


def test_a_push_that_breaks_nothing_goes_through(repo, tmp_path):
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 2\n")
    log = tmp_path / "log"
    done = landing(repo, log)
    assert done.returncode == 0, done.stderr
    assert [c["argv"] for c in calls(log)] == [["all", "tests/test_untouched.py", "--redteam"]]


def test_the_check_runs_the_scripts_test_of_the_checkout_with_the_pushed_files(repo, tmp_path):
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 3\n")
    commit(repo, "tests/test_unrelated.py", SKIPPED + "\n\ndef test_more():\n    assert True\n")
    log = tmp_path / "log"
    assert landing(repo, log).returncode == 0
    assert [c["argv"] for c in calls(log)] == [["all", "tests/test_unrelated.py", "tests/test_untouched.py", "--redteam"]]


def test_the_tests_run_without_the_repository_variables_git_gives_a_hook(repo, tmp_path):
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 4\n")
    log = tmp_path / "log"
    assert landing(repo, log).returncode == 0
    assert [c["git_dir"] for c in calls(log)] == [""]


def test_a_push_of_documentation_runs_no_test_file(repo, tmp_path):
    commit(repo, "README.md", "y\n")
    log = tmp_path / "log"
    done = landing(repo, log)
    assert done.returncode == 0, done.stderr
    assert calls(log) == []


def test_a_selection_that_cannot_be_made_refuses_the_push_with_its_cause(repo, tmp_path):
    commit(repo, "tests/test_dynamic.py", "import importlib\nimportlib.import_module(\n")
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 5\n")
    done = landing(repo, tmp_path / "log")
    assert done.returncode != 0
    assert "could not be selected (SyntaxError" in done.stderr
    assert not (tmp_path / "log").exists()


def test_a_run_that_ends_without_naming_a_test_refuses_the_push(repo, tmp_path):
    (repo / "scripts" / "test").write_text(EXITS_4)
    git(repo, "commit", "-q", "-am", "runner")
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 6\n")
    done = landing(repo, tmp_path / "log")
    assert done.returncode != 0
    assert "scripts/test exited 4 without naming a failing test" in done.stderr
    assert "run it again with: scripts/test all tests/test_untouched.py --redteam" in done.stderr


def test_a_checkout_without_the_runner_refuses_the_push_with_its_cause(repo, tmp_path):
    git(repo, "rm", "-q", "-f", "scripts/test")
    git(repo, "commit", "-q", "-m", "no runner")
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 7\n")
    done = landing(repo, tmp_path / "log")
    assert done.returncode != 0
    assert "scripts/test is missing" in done.stderr


def test_a_push_of_a_commit_that_is_not_checked_out_is_refused_with_the_command_that_works(repo, tmp_path):
    older = git(repo, "rev-parse", "HEAD")
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 8\n")
    done = push(repo, "0.9dev", sha=older, base=git(repo, "rev-parse", "origin/0.9dev"), CLAUDECODE="1",
                STUB_LOG=str(tmp_path / "log"))
    assert done.returncode != 0
    assert "git push origin HEAD:refs/heads/0.9dev" in done.stderr


def test_a_path_no_rule_can_place_runs_the_guards_and_says_so(repo, tmp_path):
    commit(repo, "tests/test_suite.py", "def test_guard():\n    assert True\n")
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "pyproject.toml", "[project]\nname = 'x'\n")
    log = tmp_path / "log"
    done = landing(repo, log)
    assert done.returncode == 0, done.stderr
    assert "warning: pyproject.toml" in done.stderr and "only the tree-wide guards run for it" in done.stderr
    assert [c["argv"] for c in calls(log)] == [["all", "tests/test_suite.py", "--redteam"]]


def test_deleting_a_module_runs_the_tests_that_import_it(repo, tmp_path):
    git(repo, "rm", "-q", "src/poolhouse/other.py")
    git(repo, "commit", "-q", "-m", "delete other")
    done = landing(repo, tmp_path / "log")
    assert done.returncode != 0
    assert "src/poolhouse/other.py: a deleted module" in done.stderr
    assert "the tests that import or name it run" in done.stderr
    assert "tests/test_other.py" in done.stderr and "run it again with: scripts/test all tests/test_other.py --redteam" in done.stderr


def test_a_new_module_runs_the_layer_and_wiring_guards_even_when_no_test_reaches_it(repo, tmp_path):
    commit(repo, "tests/test_suite.py", "def test_guard():\n    assert True\n")
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "src/poolhouse/brand_new.py", "VALUE = 3\n")
    log = tmp_path / "log"
    done = landing(repo, log)
    assert done.returncode == 0, done.stderr
    assert [c["argv"] for c in calls(log)] == [["all", "tests/test_suite.py", "--redteam"]]


def test_an_edit_of_a_module_that_no_test_reaches_runs_no_guard(repo, tmp_path):
    commit(repo, "tests/test_suite.py", "def test_guard():\n    assert True\n")
    commit(repo, "src/poolhouse/lonely.py", "VALUE = 3\n")
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "src/poolhouse/lonely.py", "VALUE = 4\n")
    log = tmp_path / "log"
    assert landing(repo, log).returncode == 0
    assert calls(log) == []


def test_a_test_that_needs_a_served_model_is_named_as_not_run(repo, tmp_path):
    commit(repo, "tests/test_untouched.py", UNTOUCHED + "\n\n# pytest.mark.real_model\n")
    git(repo, "push", "-q", "origin", "0.9dev")
    commit(repo, "src/poolhouse/mod.py", "VALUE = 1\nMORE = 10\n")
    done = landing(repo, tmp_path / "log")
    assert done.returncode == 0, done.stderr
    assert "tests/test_untouched.py hold tests marked real_model" in done.stderr


def test_the_real_runner_takes_the_tier_and_flags_the_check_runs():
    sys.path.insert(0, str(REPO / "scripts" / "hooks"))
    import affected_push
    asked = [sys.executable, str(REPO / "scripts" / "test")]
    assert affected_push.TIER == "all" and "--redteam" in affected_push.FLAGS
    ok = subprocess.run([*asked, affected_push.TIER, "--help"], capture_output=True, text=True, check=False)
    bad = subprocess.run([*asked, "no-such-tier", "--help"], capture_output=True, text=True, check=False)
    assert ok.returncode == 0 and bad.returncode == 2

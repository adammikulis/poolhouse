"""weakened-assertions reports instead of refusing where a pull request's merge judges the change: on a branch that is neither main
nor the primary checkout's, and with --report."""

from __future__ import annotations

import subprocess
import sys

import pytest
from git_world import DEV, ROOT, Project, git

HOOK = ROOT / "scripts" / "hooks" / "weakened-assertions"
GUARDED = "tests/test_change_guard.py"


@pytest.fixture
def project(tmp_path):
    made = Project(tmp_path)
    made.advance(GUARDED, "def test_a():\n    assert True\n    assert True\n")
    return made


def weaken(where):
    Project.write(where, GUARDED, "def test_a():\n    assert True\n")
    git(where, "add", GUARDED)


def hook(where, *words):
    done = subprocess.run([sys.executable, str(HOOK), *words], cwd=where, capture_output=True, text=True, timeout=60, check=False)
    return done.returncode, done.stderr


def test_a_commit_on_a_work_branch_is_told_what_got_weaker_and_goes_on(project):
    where = project.branch("work/weaker", {})
    weaken(where)
    code, said = hook(where, "--staged")
    assert code == 0 and "1 fewer assertions" in said and "attack review" in said, said


@pytest.mark.parametrize("branch", [DEV, "main", ""], ids=["development", "main", "detached"])
def test_a_commit_that_no_pull_request_judges_is_still_refused(project, branch):
    where = project.root
    if branch == "main":
        git(where, "checkout", "-q", "-b", "main")
    elif not branch:
        where = project.branch("work/detached", {})
        git(where, "checkout", "-q", "--detach")
    weaken(where)
    code, said = hook(where, "--staged")
    assert code == 1 and "Reviewed-by-second" in said, said


def test_the_pull_request_check_reports_and_the_range_check_without_report_refuses(project):
    where = project.branch("work/range", {})
    weaken(where)
    Project.commit(where, "chore: weaker")
    code, said = hook(where, "--against", DEV)
    assert code == 1 and "1 fewer assertions" in said, said
    code, said = hook(where, "--against", DEV, "--report")
    assert code == 0 and "1 fewer assertions" in said, said


def test_the_commit_and_ci_run_it_so_that_it_reports():
    step = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'weakened-assertions --against "$BASE" "${report[@]}"' in step and "report=(--report)" in step

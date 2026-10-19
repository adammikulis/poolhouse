"""The checks a landing plans for a diff: the gate and the affected tests, with the whole suite left to GitHub."""

from __future__ import annotations

import land_exec
import pytest
from land_support import DEV, Project, World, git

pytest_plugins = ["node_kit"]

GITHUB_LINE = "the full suite runs on GitHub for this commit"


@pytest.fixture
def proj(tmp_path):
    (tmp_path / "git").mkdir()
    return Project(tmp_path / "git")


@pytest.fixture
def world(tmp_path, workspace_node):
    return World(tmp_path, workspace_node)


def planned(proj, files):
    """The plan for a branch of ``files`` against the development branch."""
    tree = proj.branch("work", files)
    return land_exec.plan(tree, git(proj.root, "rev-parse", DEV), git(tree, "rev-parse", "HEAD"))


@pytest.mark.parametrize("files", [
    {"tests/conftest.py": "VALUE = 1\n"},
    {"scripts/test_runner_extra.py": "VALUE = 1\n"},
    {"packaging/build_extra.py": "VALUE = 1\n"},
    {"pyproject.toml": "[project]\n"},
    {"newarea/thing.txt": "a\n"},
    {f"tests/test_many_{n}.py": "import poolhouse.mod\n" for n in range(125)},
    {"tests/test_long.py": "import poolhouse.mod\n" + "# line\n" * 4100},
], ids=["conftest", "test-runner", "packaging", "pyproject", "unmapped", "many-files", "many-lines"])
def test_a_code_diff_plans_the_gate_and_never_the_whole_suite_locally(proj, files):
    plan = planned(proj, files)
    assert plan.kind == "code"
    assert plan.checks[0].name == "gate"
    assert [c.name for c in plan.checks if c.name not in ("gate", "affected")] == []
    assert plan.ci == GITHUB_LINE
    assert all(c.argv[-1] != "full" for c in plan.checks)


def test_a_docs_diff_plans_no_github_note(proj):
    plan = planned(proj, {"docs/a.md": "changed\n"})
    assert [c.name for c in plan.checks] == ["budgets", "clean-diff"]
    assert plan.ci == ""


def test_a_dry_run_says_the_full_suite_runs_on_github(world):
    tree, _ = world.start("shared", {"tests/test_new.py": "import poolhouse.mod\n"})
    code, out = world.land(tree, "--dry-run")
    assert code == 0 and GITHUB_LINE in out, out
    assert "whole suite because" not in out


def test_a_landing_of_a_large_diff_runs_the_gate_and_the_affected_tests_only(world):
    files = {f"tests/test_many_{n}.py": "import poolhouse.mod\n" for n in range(125)}
    tree, sha = world.start("large", files)
    code, out = world.land(tree)
    assert code == 0, out
    calls = [c for c in world.proj.calls() if not c.startswith("ENV")]
    assert [c.split()[0] for c in calls] == ["gate", "all"], calls
    assert "full" not in [c.split()[0] for c in calls]
    assert GITHUB_LINE in out, out
    assert world.proj.origin_head() == sha

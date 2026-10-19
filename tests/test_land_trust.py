"""The lander reads origin, the repository and its environment as they are stored, whatever the local refs, config and environment say."""

from __future__ import annotations

from pathlib import Path

import land_git
import pytest
from land_support import DEV, ROOT, World, git

pytest_plugins = ["node_kit"]

SENSITIVE = "src/poolhouse/credentials/example.py"


@pytest.fixture
def world(tmp_path, workspace_node):
    return World(tmp_path, workspace_node)


def recording_hook(world: World) -> Path:
    """Install a pre-push hook that appends the workspace agent of the pushing environment to a file, and return the file."""
    hooks = Path(git(world.proj.root, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks"
    hooks.mkdir(exist_ok=True)
    seen = world.proj.base / "pushed-as"
    (hooks / "pre-push").write_text(f'#!/bin/sh\necho "agent=$POOLHOUSE_WORKSPACE_AGENT" >> "{seen}"\nexit 0\n', encoding="utf-8")
    (hooks / "pre-push").chmod(0o755)
    return seen


def test_a_branch_named_like_the_remote_tracking_ref_does_not_stand_for_it(world):
    tree, sha = world.start("shadowed", {SENSITIVE: "VALUE = 1\n"})
    git(tree, "branch", f"origin/{DEV}", sha)
    code, out = world.land(tree)
    assert code == 5 and "needs review" in out and "already on" not in out, out
    assert world.proj.origin_head() != sha


def test_a_remote_tracking_ref_that_fetch_does_not_restore_stops_the_landing(world):
    tree, sha = world.start("moved", {SENSITIVE: "VALUE = 1\n"})
    git(tree, "config", "remote.origin.fetch", "+refs/heads/nothing:refs/remotes/origin/nothing")
    git(tree, "update-ref", f"refs/remotes/origin/{DEV}", sha)
    code, out = world.land(tree)
    assert code == 5 and "on origin" in out and "fetch" in out and "already on" not in out, out
    assert world.proj.origin_head() != sha and tree.exists()


def test_the_landing_agent_is_the_one_the_push_check_asks_as(world):
    seen = recording_hook(world)
    tree, sha = world.start("named", {"docs/a.md": "changed\n"})
    code, out = world.land(tree, "--agent", world.lander.name, hostile={"POOLHOUSE_WORKSPACE_AGENT": ""})
    assert code == 0, out
    assert seen.read_text().split() == [f"agent={world.lander.name}"]
    assert world.proj.origin_head() == sha


def test_a_variable_that_adds_git_configuration_does_not_switch_off_the_push_check(world, tmp_path):
    seen = recording_hook(world)
    empty = tmp_path / "no-hooks"
    empty.mkdir()
    tree, sha = world.start("configured", {"docs/a.md": "changed\n"})
    hostile = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": str(empty)}
    code, out = world.land(tree, hostile=hostile)
    assert code == 0, out
    assert seen.exists(), "the push ran without the repository's pre-push hook"
    assert world.proj.origin_head() == sha


def test_a_replace_ref_does_not_change_what_the_lander_reads(world):
    tree, sha = world.start("replaced", {SENSITIVE: "VALUE = 1\n"})
    base = world.proj.origin_head()
    git(tree, "replace", sha, git(tree, "commit-tree", "-p", base, "-m", "docs: nothing", f"{base}^{{tree}}"))
    assert land_git.sealed(tree, "diff-tree", "-r", "--name-only", base, sha).stdout.strip() == SENSITIVE


def test_a_grafts_file_does_not_change_the_history_the_lander_reads(world):
    tree, sha = world.start("grafted", {"docs/a.md": "changed\n"})
    info = Path(git(tree, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "info"
    info.mkdir(exist_ok=True)
    (info / "grafts").write_text(f"{sha}\n", encoding="utf-8")
    assert land_git.sealed(tree, "rev-list", "--count", sha).stdout.strip() == "2"


def test_a_variable_that_moves_the_repository_does_not_reach_the_lander_git_calls(world, monkeypatch, tmp_path):
    tree, _ = world.start("moved-repo", {"docs/a.md": "changed\n"})
    elsewhere = tmp_path / "elsewhere"
    git(tmp_path, "init", "-q", "-b", "other", str(elsewhere))
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    assert land_git.out(tree, "branch", "--show-current") == "moved-repo"


def test_a_variable_that_adds_configuration_does_not_reach_the_lander_git_calls(world, monkeypatch):
    tree, _ = world.start("configured-git", {"docs/a.md": "changed\n"})
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.editor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "/nowhere")
    assert land_git.git(tree, "config", "--get", "core.editor", check=False).stdout.strip() == ""


def altered_lander() -> str:
    """The lander's land_steps.py as a branch might ship it: it ends the run before anything is checked."""
    return (ROOT / "scripts" / "land_steps.py").read_text(encoding="utf-8") + "\nraise SystemExit(0)\n"


def test_a_branch_that_alters_its_own_lander_is_landed_by_the_development_lander(world):
    tree, sha = world.start("own-lander", {"scripts/land_steps.py": altered_lander(), "tests/test_bad.py": "# FAILME\n"})
    code, out = world.land(tree, "--dry-run")
    assert code == 0 and "review needed (first sensitive path scripts/land_steps.py)" in out, out
    world.accept("own-lander", sha)
    code, out = world.land(tree)
    assert code == 5 and "tests failed: tests/test_bad.py::test_x" in out, out
    assert world.proj.calls()[0] == "gate"
    assert world.proj.origin_head() != sha and tree.exists()


@pytest.mark.parametrize("damage", ["edit", "extra"])
def test_a_primary_checkout_whose_lander_differs_from_origins_is_refused(world, damage):
    tree, sha = world.start("stale-lander", {"docs/a.md": "changed\n"})
    scripts = world.proj.root / "scripts"
    name = "land_exec.py" if damage == "edit" else "land_extra.py"
    if damage == "edit":
        (scripts / name).write_text((scripts / name).read_text() + "\n# local\n", encoding="utf-8")
    else:
        (scripts / name).write_text("print('extra')\n", encoding="utf-8")
    code, out = world.land(tree)
    assert code == 5 and f"scripts/{name} in the primary checkout" in out and "differs from origin/" + DEV in out, out
    assert world.proj.origin_head() != sha and world.proj.calls() == []


def test_a_primary_checkout_whose_imported_lander_module_differs_from_origins_is_refused(world):
    tree, sha = world.start("stale-import", {"docs/a.md": "changed\n"})
    module = world.proj.root / "scripts" / "queue_doctor.py"
    module.write_text(module.read_text() + "\n# local\n", encoding="utf-8")
    code, out = world.land(tree)
    assert code == 5 and "scripts/queue_doctor.py in the primary checkout" in out and "differs from origin/" + DEV in out, out
    assert world.proj.origin_head() != sha and world.proj.calls() == []


def test_a_script_the_lander_does_not_import_may_differ_in_the_primary_checkout(world):
    tree, _ = world.start("unrelated-script", {"docs/a.md": "changed\n"})
    (world.proj.root / "scripts" / "budgets").write_text("import sys\nsys.exit(0)\n# local\n", encoding="utf-8")
    code, out = world.land(tree, "--dry-run")
    assert code == 0 and "differs from origin/" not in out, out


def test_the_lander_set_follows_imports_through_other_modules():
    files = {"scripts/land": b"import land_a\n", "scripts/land_a.py": b"def f():\n    import other\n", "scripts/other.py": b"from third import x\n",
             "scripts/third.py": b"import json\n", "scripts/unused.py": b"x = 1\n"}
    assert land_git.lander_modules(files.get) == {"scripts/land", "scripts/land_a.py", "scripts/other.py", "scripts/third.py"}

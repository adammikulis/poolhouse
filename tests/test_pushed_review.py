"""An agent's push of the development branch needs the independent accept its diff calls for, whoever pushes and however."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from land_support import DEV, ROOT, World, git

pytest_plugins = ["node_kit"]

SENSITIVE = "src/poolhouse/credentials/example.py"


@pytest.fixture
def world(tmp_path, workspace_node):
    return World(tmp_path, workspace_node)


@pytest.fixture
def pushed():
    loader = importlib.machinery.SourceFileLoader("pushed_hook", str(ROOT / "scripts" / "hooks" / "pushed"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("pushed_hook", loader))
    loader.exec_module(module)
    return module


@pytest.fixture
def as_lander(world, monkeypatch):
    for key, value in world.env(world.lander).items():
        monkeypatch.setenv(key, value)
    return world


def push_line(tree, tip, remote=None):
    """The line git gives the hook: the commit the remote holds is what the remote advertised, whatever the local refs say."""
    held = remote or git(tree, "rev-parse", f"refs/remotes/origin/{DEV}")
    return f"refs/heads/{git(tree, 'branch', '--show-current')} {tip} refs/heads/{DEV} {held}"


def test_a_sensitive_tip_without_an_accept_is_refused(as_lander, pushed, monkeypatch):
    tree, sha = as_lander.start("sensitive", {SENSITIVE: "VALUE = 1\n"})
    monkeypatch.chdir(tree)
    why = pushed.unreviewed(DEV, sha, as_lander.proj.origin_head())
    assert "needs review" in why and f"land-review sensitive {sha}" in why, why


def test_the_hook_refuses_the_push_with_the_reason(as_lander, pushed, monkeypatch, capsys):
    tree, sha = as_lander.start("refused", {SENSITIVE: "VALUE = 1\n"})
    monkeypatch.chdir(tree)
    assert pushed.main(DEV, [push_line(tree, sha)]) == 1
    assert "needs an independent review" in capsys.readouterr().err


def test_an_independent_accept_of_exactly_the_tip_passes(as_lander, pushed, monkeypatch):
    tree, sha = as_lander.start("accepted", {SENSITIVE: "VALUE = 1\n"})
    as_lander.accept("accepted", sha)
    monkeypatch.chdir(tree)
    assert pushed.unreviewed(DEV, sha, as_lander.proj.origin_head()) == ""


def test_the_creators_own_accept_does_not_pass(as_lander, pushed, monkeypatch):
    tree, sha = as_lander.start("own", {SENSITIVE: "VALUE = 1\n"})
    as_lander.accept("alias", sha, who=as_lander.creator)
    git(tree, "branch", "alias", sha)
    monkeypatch.chdir(tree)
    assert pushed.unreviewed(DEV, sha, as_lander.proj.origin_head()), "an accept the creator wrote under another name, for a branch nobody claimed"


def test_an_ordinary_tip_needs_no_answer_from_the_node(as_lander, pushed, monkeypatch):
    tree, sha = as_lander.start("ordinary", {"docs/a.md": "changed\n"})
    as_lander.node.stop()
    monkeypatch.chdir(tree)
    assert pushed.unreviewed(DEV, sha, as_lander.proj.origin_head()) == ""


def test_a_node_that_cannot_answer_refuses_a_sensitive_tip(as_lander, pushed, monkeypatch, tmp_path):
    tree, sha = as_lander.start("silent", {SENSITIVE: "VALUE = 1\n"})
    as_lander.accept("silent", sha)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "no-node-here"))
    monkeypatch.chdir(tree)
    assert "could not be asked" in pushed.unreviewed(DEV, sha, as_lander.proj.origin_head())


def test_a_tip_no_branch_points_at_is_refused(as_lander, pushed, monkeypatch):
    tree, sha = as_lander.start("headless", {SENSITIVE: "VALUE = 1\n"})
    git(tree, "switch", "-q", "--detach")
    git(as_lander.proj.root, "branch", "-D", "headless")
    monkeypatch.chdir(as_lander.proj.root)
    assert "push a branch tip" in pushed.unreviewed(DEV, sha, as_lander.proj.origin_head())


def test_a_direct_push_of_a_sensitive_tip_without_an_accept_is_refused_by_the_installed_hook(world):
    tree, sha = world.start("direct", {SENSITIVE: "VALUE = 1\n"})
    hook = Path(git(tree, "rev-parse", "--path-format=absolute", "--git-common-dir")) / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexec sh " + shlex.quote((ROOT / "scripts/hooks/pre-push").as_posix()) + ' "$@"\n', encoding="utf-8")
    hook.chmod(0o755)
    env = {**world.env(world.lander), "CLAUDECODE": "1", "PYTHON": sys.executable}
    done = subprocess.run(["git", "push", "origin", f"{sha}:refs/heads/{DEV}"], cwd=tree, env=env, capture_output=True, text=True, check=False)
    assert done.returncode != 0 and "needs an independent review" in done.stderr, done.stderr
    assert world.proj.origin_head() != sha


MOVES = {
    "update-ref": lambda tree, sha: git(tree, "update-ref", f"refs/remotes/origin/{DEV}", sha),
    "fetch": lambda tree, sha: git(tree, "fetch", "-q", ".", f"{sha}:refs/remotes/origin/{DEV}"),
    "branch": lambda tree, sha: git(tree, "branch", f"origin/{DEV}", sha)}


@pytest.mark.parametrize("how", sorted(MOVES))
def test_a_local_ref_moved_to_the_tip_does_not_hide_a_sensitive_diff(as_lander, pushed, monkeypatch, capsys, how):
    tree, sha = as_lander.start("moved", {SENSITIVE: "VALUE = 1\n"})
    line = push_line(tree, sha)
    MOVES[how](tree, sha)
    assert git(tree, "rev-parse", f"origin/{DEV}") == sha
    monkeypatch.chdir(tree)
    assert pushed.main(DEV, [line]) == 1
    assert "needs an independent review" in capsys.readouterr().err


@pytest.mark.parametrize("held", ["1" * 40, "0" * 40])
def test_a_remote_commit_this_checkout_lacks_makes_the_whole_tip_need_review(as_lander, pushed, monkeypatch, capsys, held):
    tree, sha = as_lander.start("unknown", {"docs/a.md": "changed\n"})
    monkeypatch.chdir(tree)
    assert pushed.main(DEV, [push_line(tree, sha, held)]) == 1
    assert "needs an independent review" in capsys.readouterr().err


def test_a_replace_ref_does_not_hide_a_sensitive_tip(as_lander, pushed, monkeypatch, capsys):
    tree, sha = as_lander.start("replaced", {SENSITIVE: "VALUE = 1\n"})
    base = as_lander.proj.origin_head()
    git(tree, "replace", sha, git(tree, "commit-tree", "-p", base, "-m", "docs: nothing", f"{base}^{{tree}}"))
    monkeypatch.chdir(tree)
    assert pushed.main(DEV, [push_line(tree, sha)]) == 1
    assert "needs an independent review" in capsys.readouterr().err


def test_a_check_that_cannot_load_refuses_the_push_with_a_message(as_lander, pushed, monkeypatch, capsys):
    tree, sha = as_lander.start("unloaded", {"docs/a.md": "changed\n"})
    monkeypatch.setitem(sys.modules, "land_review", None)
    monkeypatch.chdir(tree)
    assert pushed.main(DEV, [push_line(tree, sha)]) == 1
    assert "could not load" in capsys.readouterr().err

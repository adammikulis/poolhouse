"""A session that owns a worktree past the grace with undecided work cannot land more work until it decides the tree.

A real node and a real repository: `scripts/land` is run as the session that owns the tree.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from land_support import World, git

from poolhouse import trees
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]


@pytest.fixture
def world(tmp_path, workspace_node):
    return World(tmp_path, workspace_node)


def stale_tree(world: World, branch: str, owner: str) -> Path:
    """A worktree on ``branch`` with an unlanded commit, owned by ``owner``, who stopped long ago."""
    root = world.proj.root
    path = world.proj.branch(branch, {f"docs/{branch}.md": f"{branch}\n"})
    trees.claim(root, path, owner, 0.0, {"purpose": "test"})
    trees.update(root, lambda data: data["trees"][str(path.resolve())].update(state="finished", stopped=1.0))
    return path


def test_the_owner_of_an_orphan_is_refused_with_the_close_command(world):
    path = stale_tree(world, "stale", world.lander.name)
    fresh, sha = world.start("fresh", {"docs/a.md": "changed\n"})
    code, out = world.land(fresh)
    assert code == 5 and f"{world.lander.name} owns 1 worktree" in out, out
    assert f"scripts/worktrees close {path.resolve()} --bundle" in out
    assert world.proj.origin_head() != sha
    trees.close(world.proj.root, path, ("bundle", ""), time.time(), world.lander.name)
    code, out = world.land(fresh)
    assert code == 0, out
    assert world.proj.origin_head() == sha


def test_another_session_is_not_stopped_by_the_orphan(world):
    stale_tree(world, "stale", world.lander.name)
    fresh, sha = world.start("fresh", {"docs/a.md": "changed\n"})
    code, out = world.land(fresh, who=world.other)
    assert code == 0, out
    assert world.proj.origin_head() == sha


def test_the_owner_may_land_the_branch_the_orphan_holds(world):
    path = stale_tree(world, "stale", world.lander.name)
    held = git(path, "rev-parse", "HEAD")
    code, out = world.land(path)
    assert code == 0, out
    assert world.proj.origin_head() == held


def test_a_tree_within_the_grace_does_not_stop_its_owner(world):
    path = stale_tree(world, "waiting", world.lander.name)
    trees.update(world.proj.root, lambda data: data["trees"][str(path.resolve())].update(stopped=time.time() - 60))
    fresh, sha = world.start("fresh", {"docs/a.md": "changed\n"})
    code, out = world.land(fresh)
    assert code == 0, out
    assert world.proj.origin_head() == sha


def test_a_session_outside_any_repository_is_not_refused(world, tmp_path, monkeypatch):
    stale_tree(world, "stale", world.lander.name)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert landing.undecided_trees(world.lander.name, "fresh") == ""

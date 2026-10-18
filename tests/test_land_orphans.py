"""A session that owns a worktree past the grace with undecided work cannot queue more work until it decides the tree.

A real node and a real repository: the request goes through `landing.request`, the code `poolhouse-workspace land-request` runs.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from land_support import git
from land_world import World, mod

from poolhouse import trees
from poolhouse.board.client import Denied
from poolhouse.workspace import landing

pytest_plugins = ["node_kit"]


@pytest.fixture
def world(monkeypatch, tmp_path, workspace_node):
    made = World(monkeypatch, tmp_path, workspace_node)
    monkeypatch.chdir(made.proj.root)
    return made


def stale_tree(world: World, branch: str, owner: str) -> Path:
    """A worktree on ``branch`` with an unlanded commit, owned by ``owner``, who stopped long ago."""
    root = world.proj.root
    path = root.parent / f"tree-{branch}"
    git(root, "worktree", "add", "-q", "-b", branch, str(path))
    (path / f"{branch}.txt").write_text(branch)
    git(path, "add", f"{branch}.txt")
    git(path, "commit", "-qm", "work", "--no-verify")
    trees.claim(root, path, owner, 0.0, {"purpose": "test"})
    trees.update(root, lambda data: data["trees"][str(path.resolve())].update(state="finished", stopped=1.0))
    return path


def refused(world: World, who: str, branch: str, n: int):
    """The Denied raised when ``who`` asks to land ``branch`` (made from mod(n)) with an orphan in the way."""
    sha = world.branch(branch, mod(n))
    with pytest.raises(Denied, match="owns 1 worktree") as caught:
        landing.request(world.who[who], {"branch": branch, "sha": sha, "selectors": [f"tests/test_m{n}.py"]})
    return caught.value, sha


def test_a_request_from_the_owner_of_an_orphan_is_refused_with_the_close_command(world):
    alice = world.who["alice"]
    path = stale_tree(world, "stale", alice.name)
    error, sha = refused(world, "alice", "fresh", 1)
    assert f"{alice.name} owns 1 worktree" in str(error)
    assert f"scripts/worktrees close {path.resolve()} --bundle" in str(error)
    assert world.requests() == {}
    rid = world.ask("bob", "other", world.branch("other", mod(2)))
    assert world.status(rid) == "needs-review"
    trees.close(world.proj.root, path, ("bundle", ""), time.time(), "alice")
    assert world.status(world.ask("alice", "fresh", sha)) == "needs-review"


def test_the_owner_may_ask_to_land_the_branch_the_orphan_holds(world):
    alice = world.who["alice"]
    stale_tree(world, "stale", alice.name)
    refused(world, "alice", "fresh", 3)
    held = git(world.proj.root, "rev-parse", "stale")
    rid = landing.request(alice, {"branch": "stale", "sha": held, "selectors": ["tests/test_m3.py"]})["id"]
    assert world.status(rid) == "needs-review"


def test_a_tree_within_the_grace_does_not_stop_its_owner(world):
    path = stale_tree(world, "waiting", world.who["alice"].name)
    _, sha = refused(world, "alice", "fresh", 4)
    trees.update(world.proj.root, lambda data: data["trees"][str(path.resolve())].update(stopped=time.time() - 60))
    assert world.status(world.ask("alice", "fresh", sha)) == "needs-review"


def test_a_request_outside_any_repository_is_not_refused(world, tmp_path, monkeypatch):
    stale_tree(world, "stale", world.who["alice"].name)
    sha = world.branch("fresh", mod(5))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert landing.undecided_trees(world.who["alice"].name, "fresh") == ""
    assert landing.request(world.who["alice"], {"branch": "fresh", "sha": sha, "selectors": ["tests/test_m5.py"],
                                                 "target": "devline"})["id"]

"""scripts/sync-dev against a real bare origin and a real clone: it fast-forwards only a clean, behind development branch."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync-dev"
DEV = "devline"


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    done = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def commit(repo: Path, name: str) -> None:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)


class World:
    """An origin, a clone on the development branch and a second clone that pushes to it."""

    def __init__(self, tmp_path: Path) -> None:
        self.origin = tmp_path / "origin.git"
        git(tmp_path, "init", "-q", "--bare", "-b", DEV, str(self.origin))
        self.pusher = tmp_path / "pusher"
        git(tmp_path, "clone", "-q", str(self.origin), str(self.pusher))
        git(self.pusher, "checkout", "-q", "-B", DEV)
        commit(self.pusher, "base")
        git(self.pusher, "push", "-q", "origin", DEV)
        self.work = tmp_path / "work"
        git(tmp_path, "clone", "-q", str(self.origin), str(self.work))

    def push_more(self, name: str = "more") -> str:
        commit(self.pusher, name)
        git(self.pusher, "push", "-q", "origin", DEV)
        return git(self.pusher, "rev-parse", "HEAD")

    def sync(self) -> str:
        env = {**os.environ, "POOLHOUSE_DEV_BRANCH": DEV}
        done = subprocess.run(["sh", str(SCRIPT)], cwd=self.work, env=env, capture_output=True, text=True, check=False)
        assert done.returncode == 0, done.stderr
        lines = done.stdout.strip().splitlines()
        assert len(lines) == 1 and lines[0].startswith("sync-dev: "), done.stdout
        return lines[0]

    def head(self) -> str:
        return git(self.work, "rev-parse", "HEAD")


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def test_behind_and_clean_fast_forwards(world: World) -> None:
    tip = world.push_more()
    assert "fast-forwarded" in world.sync()
    assert world.head() == tip


def test_dirty_tree_is_left_alone(world: World) -> None:
    world.push_more()
    before = world.head()
    (world.work / "base").write_text("changed")
    assert "working tree has changes" in world.sync()
    assert world.head() == before
    assert (world.work / "base").read_text() == "changed"


def test_diverged_branch_is_left_alone(world: World) -> None:
    world.push_more()
    commit(world.work, "local")
    before = world.head()
    assert "each moved on" in world.sync()
    assert world.head() == before


def test_non_dev_branch_is_left_alone(world: World) -> None:
    world.push_more()
    git(world.work, "checkout", "-q", "-b", "feature")
    before = world.head()
    assert "not " + DEV in world.sync()
    assert world.head() == before

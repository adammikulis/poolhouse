"""A link in a session's scratch space is never followed: not in a folder, not as a folder, not as the session's directory or the root of all."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node):
    node = workspace_node
    node.alpha, node.beta = node.member("alpha"), node.member("beta")
    return node


def run(node, who, *argv):
    done = node.cli(*argv, "--json", who=who)
    assert done.returncode == 0, (argv, done.stderr, done.stdout)
    return json.loads(done.stdout)


def outside(tmp_path: Path) -> Path:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "keep").write_text("x")
    return elsewhere


def test_a_link_inside_a_folder_cannot_lead_out_and_is_not_counted_or_removed(team, tmp_path):
    folder = Path(run(team, team.alpha, "scratch-new", "build")["path"])
    secret = outside(tmp_path)
    (folder / "door").symlink_to(secret, target_is_directory=True)
    assert team.cli("scratch-path", "build", "door/keep", who=team.alpha).returncode == 3
    assert Path(run(team, team.alpha, "scratch-path", "build", "plain.txt")["path"]) == folder / "plain.txt"
    before = run(team, team.alpha, "scratch-ls")[0]["bytes"]
    (secret / "large.bin").write_bytes(b"x" * 2000)
    assert run(team, team.alpha, "scratch-ls")[0]["bytes"] == before
    assert run(team, team.alpha, "scratch-rm", "build") == {"removed": True}
    assert (secret / "keep").exists() and (secret / "large.bin").exists()


def test_a_folder_or_a_session_directory_that_is_a_link_is_not_followed(team, tmp_path):
    first = Path(run(team, team.alpha, "scratch-new", "first")["path"])
    secret = outside(tmp_path)
    (first.parent / "linked").symlink_to(secret, target_is_directory=True)
    assert team.cli("scratch-path", "linked", "keep", who=team.alpha).returncode == 3
    assert run(team, team.alpha, "scratch-rm", "linked") == {"removed": False}
    assert team.cli("scratch-new", "linked", who=team.alpha).returncode == 3
    assert (secret / "keep").exists() and (first.parent / "linked").is_symlink()
    beta_directory = first.parent.parent / team.beta.name
    beta_directory.symlink_to(secret, target_is_directory=True)
    assert team.cli("scratch-new", "x", who=team.beta).returncode == 3
    assert sorted(path.name for path in secret.iterdir()) == ["keep"]


@pytest.mark.redteam
def test_a_link_as_the_scratch_root_changes_nothing_and_creates_no_session_directory_in_its_target(team, tmp_path):
    root = team.state / "scratch"
    shutil.rmtree(root, ignore_errors=True)
    target = tmp_path / "outside"
    target.mkdir()
    (target / "kept.txt").write_text("preserved")
    root.symlink_to(target, target_is_directory=True)
    refused = team.cli("scratch-new", "build", who=team.alpha)
    assert refused.returncode == 3, (refused.stdout, refused.stderr)
    assert sorted(path.name for path in target.iterdir()) == ["kept.txt"]
    assert (target / "kept.txt").read_text() == "preserved"

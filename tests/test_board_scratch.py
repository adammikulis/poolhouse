"""Scratch folders the node keeps for a session: make, list, resolve a path inside, remove, and who may reach whose."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node):
    node = workspace_node
    node.lead, node.bob = node.member("main-1"), node.member("bob")
    return node


def run(team, who, *argv):
    done = team.cli(*argv, "--json", who=who)
    assert done.returncode == 0, (argv, done.stderr, done.stdout)
    return json.loads(done.stdout)


def test_a_session_makes_lists_resolves_and_removes_its_own_folders(team):
    lead = team.lead
    path = run(team, lead, "scratch-new", "work", "--ttl-hours", "2")["path"]
    assert Path(path).is_dir() and oct(Path(path).stat().st_mode & 0o777) == "0o700"
    (Path(path) / "a.txt").write_text("12345")
    row = run(team, lead, "scratch-ls")[0]
    assert (row["owner"], row["name"], row["path"], row["over_limit"], row["expired"]) == (lead.name, "work", path, False, False)
    assert row["bytes"] >= 5 and 1.9 * 3600 < row["expires"] - time.time() < 2.1 * 3600
    assert run(team, lead, "scratch-path", "work", "sub/../a.txt")["path"] == f"{path}/a.txt"
    assert run(team, lead, "scratch-rm", "work") == {"removed": True}
    assert run(team, lead, "scratch-rm", "work") == {"removed": False} and not Path(path).exists()
    assert "work" not in team.cli("scratch-ls", who=lead).stdout


def test_a_path_never_leaves_its_folder_and_a_name_is_a_plain_word(team):
    lead = team.lead
    path = run(team, lead, "scratch-new", "work")["path"]
    for bad in ("../x", "a/../../x", "/etc/passwd", ".."):
        done = team.cli("scratch-path", "work", bad, who=lead)
        assert done.returncode == 3 and "outside the scratch folder" in done.stderr, bad
    (Path(path) / "out").symlink_to("/tmp")
    assert team.cli("scratch-path", "work", "out/x", who=lead).returncode == 3
    for bad in ("A", "../up", "a/b", ".hidden..x", "x" * 65):
        assert team.cli("scratch-new", bad, who=lead).returncode == 2, bad
    assert team.cli("scratch-path", "../work", who=lead).returncode == 3


def test_private_folders_stay_with_their_owner_and_the_sessions_above_it(team):
    lead, bob = team.lead, team.bob
    kid = team.member("kid", parent=lead)
    run(team, lead, "scratch-new", "mine")
    refused = team.cli("scratch-ls", "--for-agent", lead.name, "--json", who=bob)
    assert refused.returncode == 3 and "cannot reach" in refused.stderr + refused.stdout
    for argv in (("scratch-path", "mine", "--for-agent", lead.name), ("scratch-rm", "mine", "--for-agent", lead.name)):
        assert team.cli(*argv, who=bob).returncode == 3
    assert Path(run(team, lead, "scratch-path", "mine")["path"]).is_dir()
    assert team.cli("scratch-new", "kids", who=kid).returncode == 3, "a spawned subagent has no scratch space"
    assert run(team, lead, "scratch-ls", "--for-agent", kid.name) == []
    assert run(team, lead, "scratch-ls", "--for-agent", lead.name)[0]["name"] == "mine"
    ambiguous = team.cli("scratch-ls", "--for-agent", "claude-", who=lead)
    assert ambiguous.returncode == 3 and lead.name in ambiguous.stdout + ambiguous.stderr
    assert team.cli("scratch-ls", "--for-agent", "nobody-at-all", who=lead).returncode == 3

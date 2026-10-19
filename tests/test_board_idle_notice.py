"""The node tells a parent that it retired the parent's idle subagent."""

from __future__ import annotations

import json
import time

import pytest

pytest_plugins = ["node_kit"]

NODE_WRITE_LIMITS = {"sends": 1_000_000, "subagent_sends": 1_000_000, "idle_retire_s": 3, "child_beat_s": 1}


@pytest.fixture
def team(workspace_node):
    workspace_node.lead = workspace_node.member("lead")
    return workspace_node


def test_a_subagent_retired_for_idleness_is_told_to_its_parent(team):
    child = team.member("child", parent=team.lead)
    assert team.cli("claim", "port", "9483", who=team.lead).returncode == 0     # a lease keeps the parent from being idle
    time.sleep(3.6)
    listed = team.cli("agents", "--json", who=team.lead)
    assert listed.returncode == 0, listed.stderr
    done = team.cli("inbox", "--json", who=team.lead)
    assert done.returncode == 0, done.stderr
    shown = json.loads(done.stdout)
    rows = shown if isinstance(shown, list) else shown.get("items", [])
    told = [row for row in rows if row.get("from") == "node"]
    assert len(told) == 1
    assert child.name in told[0]["text"] and "idle" in told[0]["text"] and "retired" in told[0]["text"]

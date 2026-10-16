"""What the node tells a session on its own account: a claim that ran out, a subagent retired for idleness, a token that is gone."""

from __future__ import annotations

import json
import time

import pytest

from poolhouse.board.client import Denied

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node):
    workspace_node.lead = workspace_node.member("lead")
    return workspace_node


def notices(team, who) -> list[dict]:
    """The messages the node wrote to ``who``."""
    done = team.cli("inbox", "--json", who=who)
    assert done.returncode == 0, done.stderr
    shown = json.loads(done.stdout)
    rows = shown if isinstance(shown, list) else shown.get("items", shown.get("messages", []))
    return [row for row in rows if row.get("from") == "node"]


def test_a_claim_that_ran_out_is_told_to_its_holder_with_the_command_to_take_it_again(team):
    assert team.cli("claim", "port", "9481", "--ttl", "1", who=team.lead).returncode == 0
    time.sleep(2.5)
    assert team.cli("claims", who=team.lead).returncode == 0
    told = notices(team, team.lead)
    assert len(told) == 1
    text = told[0]["text"]
    assert "port 9481" in text and "expired" in text and "poolhouse-workspace claim port 9481" in text
    assert "heartbeat" in text


def test_a_claim_released_in_time_tells_nobody(team):
    assert team.cli("claim", "port", "9482", "--ttl", "1", who=team.lead).returncode == 0
    assert team.cli("release", "port", "9482", who=team.lead).returncode == 0
    time.sleep(2.5)
    assert team.cli("claims", who=team.lead).returncode == 0
    assert notices(team, team.lead) == []


def test_a_retired_sessions_next_call_says_it_was_retired(team):
    child = team.member("child", parent=team.lead)
    session = team.session(child)
    team.session(team.lead).retire(child.name)
    with pytest.raises(Denied, match="was retired"):
        session.whoami()

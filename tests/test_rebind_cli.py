"""`poolhouse-workspace rebind` against a real node: a terminal takes a main session over at once, with nothing signed, typed or tapped.
Nothing requires it (a session registers from any process by itself, tests/test_node_lineage.py); it is for the one case where a live
sibling is to give way now."""
from __future__ import annotations

import json

import pytest

from poolhouse.board import session as board_session
from poolhouse.board.client import Denied

pytest_plugins = ["node_kit"]


@pytest.fixture
def lead(workspace_node):
    workspace_node.lead = workspace_node.member("lead")
    return workspace_node


def test_a_terminal_runs_rebind_and_the_session_is_its_own_at_once_with_no_question_asked(lead):
    node = lead
    code, out, err = node.terminal("rebind", "claude-code", "lead", "--json", timeout=120)
    assert code == 0, (out, err)
    said = json.loads(out[out.index("{"):])
    assert said["moved"] == node.lead.name and said["tokens_ended"] >= 1, said
    for word in ("Type yes", "phone", "approve"):
        assert word not in out + err, (word, out, err)
    with pytest.raises(Denied, match="the token is not known"):
        node.session(node.lead).whoami()
    again = board_session.register(node.client, node.board, board_session.Native("claude-sonnet-5-5", "claude-code", "lead"))
    assert again.name == node.lead.name, "the harness started in that terminal registers as the session"
    assert node.session(node.lead).client.call("whoami", node.board, again.token)["name"] == node.lead.name


def test_rebind_of_a_session_nobody_registered_changes_nothing_and_says_so(lead):
    node = lead
    code, out, err = node.terminal("rebind", "claude-code", "no-such-session", "--json", timeout=120)
    assert code != 0 and "no session of that harness and id" in out + err, (out, err)
    assert node.session(node.lead).whoami().name == node.lead.name, "nothing was ended"

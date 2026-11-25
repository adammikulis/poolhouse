"""The public side of asking for grants: `ph.board.Board.request_approval`, `ph.board.Ask` and `ph.board.Approval`, against a real node (docs/api.md).
The request is decided before it is returned, as `poolhouse-workspace approval request` decides it (tests/test_board_approval.py): this device's key signs what
the caller may have and the rest is refused with the reason, so there is no handle to wait on."""

from __future__ import annotations

import pytest
from test_computer_key import KS

import poolhouse as ph
from poolhouse import caller, keystore
from poolhouse.board import founding, grants

pytest_plugins = ["node_kit"]


def test_an_agent_asks_and_has_the_decision_at_once_with_nothing_to_wait_for(workspace_node):
    team = workspace_node
    board = ph.board.Board(team.session(team.member("lead")))
    ask = board.request_approval([ph.board.Ask("add_device", lasts="10m", uses=1)], note="adding the Linux box")
    assert isinstance(ask, ph.board.Approval)
    assert (ask.state, ask.risk) == ("approved", "sensitive") and "adding the Linux box" in ask.text and ask.title.endswith("asks to add a device")
    assert len(ask.grants) == 1 and ask.message() == "It was approved, so the grants exist."
    held = grants.listing(team.session(team.members["lead"]))
    assert [(g.action, g.status, g.carried_by) for g in held.grants] == [("add_device", "live", "node")]
    assert not {"wait", "cancel", "refresh", "code", "deduplicated", "expires_ms"} & set(dir(ask)), "a decided request has no handle to wait on, withdraw or read again"
    assert not {"approval", "approvals", "blocked_sessions", "lift_block", "offer_phone"} & set(dir(board)), "and the board has nothing to read it back with"
    assert team.session(team.members["lead"]).call("approval_pending")["approvals"] == []


def test_an_ask_for_what_no_one_may_grant_and_a_policy_the_pool_does_not_have_are_refused_with_a_sentence(workspace_node):
    team = workspace_node
    board = ph.board.Board(team.session(team.member("lead")))
    with pytest.raises(ph.Denied, match="grant_issue is a floor: no grant, from anyone, covers it"):
        board.request_approval([ph.board.Ask("grant_issue")])
    with pytest.raises(ph.Error, match=r"The pool is secure, not open as you wrote; ask again with what is true\."):
        board.request_approval([ph.board.Ask("add_device")], expect_policy="open")
    with pytest.raises(ValueError, match="a time is a whole number"):
        board.request_approval([ph.board.Ask("add_device", lasts="soon")])
    assert team.session(team.members["lead"]).call("approval_pending")["approvals"] == [], "a refused ask left nothing waiting, so the next one is not blocked by it"
    assert board.request_approval([ph.board.Ask("sync_now", uses=1)]).state == "approved"


def test_an_agent_is_refused_at_once_the_grants_only_a_person_may_have_and_no_request_is_made(workspace_node, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    team = workspace_node
    board = ph.board.Board(team.session(team.member("lead")))
    assert caller.marked()
    with pytest.raises(ph.Denied, match="member_remove is refused to an agent"):
        board.request_approval([ph.board.Ask("member_remove")])
    assert team.session(team.members["lead"]).call("approval_pending")["approvals"] == []


def test_in_a_pool_the_board_refuses_at_once_what_reaches_beyond_the_device_and_names_no_phone(workspace_node, monkeypatch):
    monkeypatch.setattr(keystore, "default", lambda: KS)
    team = workspace_node
    board = ph.board.Board(team.session(team.member("lead")))
    founding.found_here(team.client, "home", "secure")
    with pytest.raises(ph.Denied) as refused:
        board.request_approval([ph.board.Ask("note_verify", lasts="10m", uses=1)])
    assert grants.POOL_GRANTS in str(refused.value) and "phone" not in str(refused.value).lower()
    assert team.session(team.members["lead"]).call("approval_pending")["approvals"] == [], "no request was made to wait on"

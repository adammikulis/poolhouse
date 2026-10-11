"""The public side of asking a person's phone for grants: `ph.board.Board.request_approval`, `ph.board.Ask` and `ph.board.Approval`,
against a real node and a phone that is only keys and requests (docs/api.md, docs/phone-approval.md)."""

from __future__ import annotations

import pytest
from phone_kit import add_phone, endpoint_of

import poolhouse as ph
from poolhouse.board import grants

pytest_plugins = ["node_kit", "phone_kit"]


def test_an_agent_asks_waits_and_is_told_when_the_phone_approved(phone_node):
    team = phone_node
    phone = add_phone(team)
    board = ph.board.Board(team.session(team.member("lead")))
    ask = board.request_approval([ph.board.Ask("add_device", lasts="10m", uses=1)], note="adding the Linux box")
    assert isinstance(ask, ph.board.Approval)
    assert (ask.state, ask.risk) == ("pending", "sensitive") and "adding the Linux box" in ask.text and ask.title.endswith("asks to add a device")
    assert ask.wait(0.6) == "pending", "time running out is not a failure"
    assert len(ask.code) == 2 and ask.urgency == "urgent" and not ask.deduplicated, "a request that is not routine comes with the number to show the person"
    assert board.request_approval([ph.board.Ask("add_device", lasts="10m", uses=1)], note="again").deduplicated, "the same request made again is the same item"
    assert ask.message() == "Waiting for your answer on your phone."
    endpoint = endpoint_of(team)
    seen = endpoint.ok("list", token=phone.token)["approvals"][0]
    assert seen["id"] == ask.id and seen["text"] == ask.text, "the phone shows the text the agent has"
    assert endpoint.ok("decide", phone.decision(seen, "approve", pick=ask.code))["state"] == "approved"
    assert ask.wait(10) == "approved" and len(ask.grants) == 1
    assert ask.message() == "You approved it, so the grants exist."
    held = grants.listing(team.session(team.members["lead"]))
    assert [(g.action, g.status, g.carried_by) for g in held.grants] == [("add_device", "live", "node")]
    assert board.approval(ask.id).state == "approved" and [a.id for a in board.approvals()] == [ask.id]
    assert [a.id for a in board.approvals("denied")] == []


def test_a_denied_request_changes_nothing_and_a_withdrawn_one_ends(phone_node):
    team = phone_node
    phone = add_phone(team)
    board = ph.board.Board(team.session(team.member("lead")))
    endpoint = endpoint_of(team)
    first = board.request_approval([ph.board.Ask("sync_now", uses=1)])
    assert endpoint.ok("decide", phone.decision(endpoint.ok("get", {"id": first.id}, token=phone.token), "deny"))["state"] == "denied"
    assert first.wait(5) == "denied" and first.message() == "You said no; nothing changed."
    with pytest.raises(ph.Error, match=r"declined a moment ago; wait .* before asking for it again"):
        board.request_approval([ph.board.Ask("sync_now", uses=1)])
    second = board.request_approval([ph.board.Ask("link_read", "a>b", uses=1)])
    assert second.cancel() == "cancelled" and second.message() == "The request was withdrawn."
    assert grants.listing(team.session(team.members["lead"])).grants == []


def test_an_ask_for_what_no_one_may_grant_and_a_policy_the_pool_does_not_have_are_refused_with_a_sentence(phone_node):
    team = phone_node
    add_phone(team)
    board = ph.board.Board(team.session(team.member("lead")))
    with pytest.raises(ph.Denied, match="grant_issue is a floor: no grant, from anyone, covers it"):
        board.request_approval([ph.board.Ask("grant_issue")])
    with pytest.raises(ph.Error, match=r"The pool is secure, not open as you wrote; ask again with what is true\."):
        board.request_approval([ph.board.Ask("add_device")], expect_policy="open")
    with pytest.raises(ValueError, match="a time is a whole number"):
        board.request_approval([ph.board.Ask("add_device", lasts="soon")])


def test_a_link_for_one_more_phone_needs_a_phone_added_first_and_never_adds_one_alone(phone_node):
    team = phone_node
    board = ph.board.Board(team.session(team.member("lead")))
    with pytest.raises(ph.Denied, match="The first phone is added with `poolhouse-workspace phone add`"):
        board.offer_phone()
    first = add_phone(team)
    link = board.offer_phone()
    assert link.startswith("poolhouse://add-phone?data=") and len(link) < 1500
    second = add_phone(team, "Tablet", by=first, link=link)
    assert second.token and len(team.client.call("person_keys")["keys"]) == 2

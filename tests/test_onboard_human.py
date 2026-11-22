"""A permit for a step that is kept from agents: minted at once for a process no agent started, refused at once, with the reason, to an agent."""

import sys

import pytest

from poolhouse import authority, caller
from poolhouse.caller import AgentRefused
from poolhouse.sentinel.permit import Permit, mint, mint_gated


def test_a_process_no_agent_started_gets_a_permit_with_no_terminal_and_no_typed_line_and_the_audit_says_who():
    assert not caller.marked() and not caller.is_terminal(sys.stdin) and not caller.is_terminal(sys.stdout)
    permit = mint("rotate", "abc")
    permit.check("rotate", "abc")
    (row,) = [r for r in caller.audit_rows() if r["event"] == "ran"]
    assert row["action"] == "rotate abc" and row["who"].startswith("user ")


@pytest.mark.parametrize("marker", caller.AGENT_MARKERS)
def test_a_process_started_by_an_agent_gets_none_at_once_with_the_reason(marker):
    with pytest.raises(AgentRefused, match="refused to an agent") as refused:
        mint("rotate", "abc", env={marker: "1"}, why="it replaces the key")
    assert marker in str(refused.value) and "it replaces the key" in str(refused.value)
    (row,) = [r for r in caller.audit_rows() if r["event"] == "refused"]
    assert row["action"] == "rotate abc" and row["why"] == "it replaces the key"
    assert [r for r in caller.audit_rows() if r["event"] == "ran"] == []


def test_a_gated_permit_follows_the_gate_an_agent_is_refused_while_it_is_held_back_and_passes_once_it_is_delegated(monkeypatch):
    with pytest.raises(AgentRefused, match="refused to an agent"):
        mint_gated("sentinel.policy", "approve-host", "mirror.example", env={"CLAUDECODE": "1"})
    mint_gated("sentinel.policy", "approve-host", "mirror.example").check("approve-host", "mirror.example")
    monkeypatch.delenv(authority.FLOOR_ENV)
    authority.set_state(["sentinel.policy"], authority.DELEGATED, by="test")
    monkeypatch.setenv("CLAUDECODE", "1")
    mint_gated("sentinel.policy", "approve-host", "mirror.example").check("approve-host", "mirror.example")
    used = [r for r in caller.audit_rows() if r["event"] == "authority.use"]
    assert len(used) == 1 and used[0]["who"].startswith("agent CLAUDECODE")


def test_a_permit_covers_one_action_on_one_subject_and_runs_out():
    permit = mint("rotate", "abc")
    with pytest.raises(AgentRefused):
        permit.check("export", "abc")
    with pytest.raises(AgentRefused):
        permit.check("rotate", "other")
    with pytest.raises(AgentRefused, match="expired"):
        permit.check("rotate", "abc", now=permit.expires + 1)


def test_a_permit_cannot_be_made_by_hand():
    forged = Permit("rotate", "abc", 9e18, object())
    with pytest.raises(AgentRefused, match="not a permit minted"):
        forged.check("rotate", "abc")

"""An agent that widens its own access: naming a lead it is not, loosening the registry, or forging the file.

Every refusal is automatic and at once: the gate or the loosening is allowed by the rule or refused with the reason, and nothing waits.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from taskboard_kit import board as _board_fixture

from poolhouse import authority, caller, home
from poolhouse.board.tasks import Tasks
from poolhouse.caller import AgentRefused
from poolhouse.workspace import authority_cli

pytest_plugins = ["node_kit"]
board = _board_fixture
LEAD = {"CLAUDECODE": "1", "POOLHOUSE_WORKSPACE_AGENT": "agent-lead01"}


def record(name: str, parent: str = "", *, token: bool = True) -> None:
    """What the node leaves beside a session's token when it registers: the token and the parent it named."""
    folder = home.state("node") / "client" / "board"
    folder.mkdir(parents=True, exist_ok=True)
    if token:
        (folder / f"{name}.token").write_text("t\n", encoding="utf-8")
    (folder / f"{name}.parent").write_text(parent + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def registry(monkeypatch, tmp_path):
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    for name in (*caller.AGENT_MARKERS, "POOLHOUSE_WORKSPACE_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))


def used() -> dict:
    return [row for row in authority.audit_rows() if row["event"] == "authority.use"][-1]


def passes_as(monkeypatch, name: str | None) -> str:
    """The agent the audit names after a marked process that calls itself ``name`` passes a delegated gate; the gate refuses nobody."""
    monkeypatch.setenv("CLAUDECODE", "1")
    if name is not None:
        monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", name)
    assert authority.require("runtime.deploy", "deploy") == authority.DELEGATED
    return used()["agent"]


def test_a_name_the_node_never_recorded_is_never_the_agent_the_audit_names(monkeypatch):
    assert passes_as(monkeypatch, None) == ""
    assert passes_as(monkeypatch, "") == ""
    assert passes_as(monkeypatch, "agent-feedface") == ""


def test_a_forged_role_record_authenticates_neither_the_alleged_root_nor_its_helper(monkeypatch):
    """A local file beside a token names no one: only the node says who a session is."""
    record("agent-aaaaaa")
    record("claude-bbbbbb", parent="agent-aaaaaa")
    record("agent-cccccc", token=False)
    other = Path(home.state("node")) / "client" / "second"
    other.mkdir(parents=True)
    (other / "agent-eeeeee.token").write_text("t\n", encoding="utf-8")
    (other / "agent-eeeeee.parent").write_text("agent-aaaaaa\n", encoding="utf-8")
    for name in ("agent-aaaaaa", "claude-bbbbbb", "agent-cccccc", "agent-eeeeee"):
        assert passes_as(monkeypatch, name) == ""


def test_a_name_that_is_a_path_or_a_pattern_is_not_a_lead(monkeypatch):
    record("agent-dddddd")
    for name in ("../agent-dddddd", "agent-dddddd/x", "*", "agent-dddddd\n", "A" * 3):
        assert passes_as(monkeypatch, name) == ""


def test_only_the_lead_the_node_registered_is_named_by_the_audit_not_its_helper_nor_a_forged_name(workspace_node, monkeypatch):
    lead = workspace_node.member("lead")
    helper = workspace_node.member("helper", parent=lead)
    assert passes_as(monkeypatch, lead.name) == lead.name
    assert passes_as(monkeypatch, helper.name) == ""
    assert passes_as(monkeypatch, "agent-feedface") == ""
    assert not [row for row in authority.audit_rows() if row["event"] == "refused"]


def loosening(names, *, env=None, project="", by="agent-lead01"):
    return authority.set_state(names, authority.DELEGATED, by=by, scope=authority.Scope(project=project), env=env)


@pytest.fixture
def strict():
    authority.set_state(["ALL"], authority.PERSON, by="agent-lead01")


def states() -> dict[str, str]:
    return {row["gate"]: row["state"] for row in authority.show()["gates"]}


def refusals() -> list[dict]:
    return [row for row in authority.audit_rows() if row["event"] == "refused"]


def test_a_lead_agent_is_refused_at_once_when_it_loosens_a_gate_and_nothing_changes(strict):
    with pytest.raises(AgentRefused, match="never widen"):
        loosening(["ALL"], env=LEAD)
    assert set(states().values()) == {authority.PERSON}
    assert [row["event"] for row in authority.audit_rows()] == ["authority.set", "refused"]
    assert "agent CLAUDECODE" in refusals()[0]["who"]


def test_a_process_with_no_agent_marker_loosens_a_gate_directly_with_no_terminal_and_no_question(strict):
    loosening(["runtime.deploy"])
    assert states()["runtime.deploy"] == authority.DELEGATED and states()["serve.wired-limit"] == authority.PERSON
    assert not refusals()


def test_the_refusal_names_every_gate_it_would_loosen_in_plain_words(strict):
    with pytest.raises(AgentRefused) as raised:
        loosening(["runtime", "serve.wired-limit"], env=LEAD)
    said = str(raised.value)
    assert "runtime.deploy" in said and "serve.wired-limit" in said and "chat.policy" not in said
    assert "deploy a new runtime build on this device" in said and "outside the agent" in said


def test_the_audit_row_of_a_loosening_names_the_gates_and_who_ran_it(strict):
    loosening(["runtime.deploy"])
    row = authority.audit_rows()[-1]
    assert (row["event"], row["gates"], row["from"], row["to"]) == ("authority.set", ["runtime.deploy"], ["person"], "delegated")
    assert row["who"].startswith("user ")


def test_a_helper_identity_cannot_loosen_even_with_no_agent_marker(strict):
    with pytest.raises(AgentRefused, match="not a helper"):
        loosening(["runtime.deploy"], by="agent-lead01/helper")
    assert states()["runtime.deploy"] == authority.PERSON


def test_setting_a_gate_that_is_already_delegated_is_no_loosening_and_refuses_no_agent():
    loosening(["ALL"], env=LEAD)
    assert not refusals() and set(states().values()) == {authority.DELEGATED}


def test_a_project_that_loosens_over_a_machine_that_is_a_person_s_is_refused_to_an_agent(strict):
    with pytest.raises(AgentRefused, match="never widen"):
        loosening(["runtime.deploy"], env=LEAD, project="alpha")
    assert authority.state_of("runtime.deploy", "alpha") == authority.PERSON
    loosening(["runtime.deploy"], project="alpha")
    assert authority.state_of("runtime.deploy", "alpha") == authority.DELEGATED
    assert authority.state_of("runtime.deploy") == authority.PERSON


def test_tightening_is_open_to_an_agent(strict):
    loosening(["ALL"])
    authority.set_state(["ALL"], authority.PERSON, by="agent-lead01", env=LEAD)
    assert set(states().values()) == {authority.PERSON} and not refusals()


def cli(action, *words, project=""):
    return argparse.Namespace(action=action, words=list(words), project=project)


def test_a_lead_agent_cannot_loosen_through_the_workspace_command(board, monkeypatch):
    authority_cli.run(cli("preset", "prod", project="alpha"), board.parent)
    monkeypatch.setenv("CLAUDECODE", "1")
    for call in (cli("set", "delegated", "ALL", project="alpha"), cli("preset", "dev", project="alpha")):
        with pytest.raises(AgentRefused, match="never widen"):
            authority_cli.run(call, board.parent)
    assert authority.state_of("sentinel.quarantine", "alpha") == authority.PERSON
    assert Tasks(board.parent).mode()["mode"] == "strict"
    assert len(refusals()) == 2


def test_a_process_with_no_agent_marker_loosens_through_the_workspace_command(board):
    authority_cli.run(cli("preset", "prod", project="alpha"), board.parent)
    authority_cli.run(cli("preset", "dev", project="alpha"), board.parent)
    assert authority.state_of("sentinel.quarantine", "alpha") == authority.DELEGATED
    assert Tasks(board.parent).mode()["mode"] == "open"
    assert not refusals()


def test_a_lead_agent_may_still_tighten_through_the_workspace_command(board, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    authority_cli.run(cli("set", "person", "sentinel"), board.parent)
    assert authority.state_of("sentinel.policy") == authority.PERSON

"""An agent that widens its own access: naming a lead it is not, loosening the registry, or forging the file."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from taskboard_kit import board as _board_fixture

from poolhouse import authority, home, person
from poolhouse.board.tasks import Tasks
from poolhouse.person import HumanRequired
from poolhouse.workspace import authority_cli

pytest_plugins = ["node_kit"]
board = _board_fixture


def record(name: str, parent: str = "", *, token: bool = True) -> None:
    """What the node leaves beside a session's token when it registers: the token and the parent it named."""
    folder = home.state("node") / "client" / "board"
    folder.mkdir(parents=True, exist_ok=True)
    if token:
        (folder / f"{name}.token").write_text("t\n", encoding="utf-8")
    (folder / f"{name}.parent").write_text(parent + "\n", encoding="utf-8")


def env(name: str | None, **more: str) -> dict[str, str]:
    marked = {"CLAUDECODE": "1", **more}
    return {**marked, "POOLHOUSE_WORKSPACE_AGENT": name} if name is not None else marked


@pytest.fixture(autouse=True)
def registry(monkeypatch, tmp_path):
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    for name in (*authority.DELEGATING, "POOLHOUSE_NONINTERACTIVE", "POOLHOUSE_WORKSPACE_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))


def passes(name: str | None) -> bool:
    try:
        return authority.require("runtime.deploy", "deploy", (False, False), env(name)) == authority.DELEGATED
    except HumanRequired:
        return False


def test_a_marked_process_with_no_name_passes_no_delegated_gate():
    assert not passes(None)
    assert not passes("")


def test_a_name_the_node_never_recorded_passes_no_delegated_gate():
    assert not passes("agent-feedface")


def test_a_name_with_a_parent_record_is_a_helper_not_a_lead():
    """A local role record authenticates neither the alleged root nor its helper."""
    record("agent-aaaaaa")
    record("claude-bbbbbb", parent="agent-aaaaaa")
    assert not passes("agent-aaaaaa")
    assert not passes("claude-bbbbbb")


def test_a_parent_record_without_a_token_is_not_a_lead():
    record("agent-cccccc", token=False)
    assert not passes("agent-cccccc")


def test_a_name_that_is_a_path_or_a_pattern_is_not_a_lead():
    record("agent-dddddd")
    for name in ("../agent-dddddd", "agent-dddddd/x", "*", "agent-dddddd\n", "A" * 3):
        assert not passes(name)


def test_a_helper_recorded_on_any_board_is_a_helper():
    record("agent-eeeeee")
    other = Path(home.state("node")) / "client" / "second"
    other.mkdir(parents=True)
    (other / "agent-eeeeee.token").write_text("t\n", encoding="utf-8")
    (other / "agent-eeeeee.parent").write_text("agent-aaaaaa\n", encoding="utf-8")
    assert not passes("agent-eeeeee")


def said(*answers: str):
    asked: list[str] = []
    lines = iter(answers)

    def typed(prompt: str) -> str:
        asked.append(prompt)
        return next(lines, "")

    return asked, typed


def loosening(names, *, typed=None, terminal=(True, True), env=None, project=""):
    return authority.set_state(names, authority.DELEGATED, by="agent-lead01", scope=authority.Scope(project=project),
                               asking=authority.Asking(terminal=terminal, env={} if env is None else env, typed=typed))


@pytest.fixture
def strict():
    authority.set_state(["ALL"], authority.PERSON, by="agent-lead01")


def states() -> dict[str, str]:
    return {row["gate"]: row["state"] for row in authority.show()["gates"]}


def test_a_lead_agent_cannot_loosen_a_gate(strict):
    asked, typed = said("yes")
    with pytest.raises(HumanRequired, match="for a person"):
        loosening(["ALL"], typed=typed, env={"CLAUDECODE": "1", "POOLHOUSE_WORKSPACE_AGENT": "agent-lead01"})
    assert set(states().values()) == {authority.PERSON} and not asked


def test_a_process_with_no_terminal_cannot_loosen_a_gate(strict):
    asked, typed = said("yes")
    with pytest.raises(HumanRequired, match="needs a terminal"):
        loosening(["runtime.deploy"], typed=typed, terminal=(False, False))
    assert states()["runtime.deploy"] == authority.PERSON and not asked


def test_a_person_who_does_not_type_yes_loosens_nothing(strict):
    for answer in ("", "y", "no", "yes please", "YES, all of it"):
        asked, typed = said(answer)
        with pytest.raises(HumanRequired, match="not confirmed"):
            loosening(["ALL"], typed=typed)
        assert set(states().values()) == {authority.PERSON} and len(asked) == 1


def test_the_question_names_every_gate_it_loosens_in_plain_words(strict):
    asked, typed = said("yes")
    loosening(["runtime", "serve.wired-limit"], typed=typed)
    assert "runtime.deploy" in asked[0] and "serve.wired-limit" in asked[0] and "chat.policy" not in asked[0]
    assert "without asking you" in asked[0]


def test_a_person_who_types_yes_loosens_and_the_audit_row_names_the_gates(strict):
    _, typed = said("yes")
    loosening(["runtime.deploy"], typed=typed)
    assert states()["runtime.deploy"] == authority.DELEGATED and states()["serve.wired-limit"] == authority.PERSON
    row = authority.audit_rows()[-1]
    assert (row["event"], row["gates"], row["from"], row["to"]) == ("authority.set", ["runtime.deploy"], ["person"], "delegated")


def test_setting_a_gate_that_is_already_delegated_asks_nothing():
    asked, typed = said()
    loosening(["ALL"], typed=typed, terminal=(False, False), env={"CLAUDECODE": "1"})
    assert not asked and set(states().values()) == {authority.DELEGATED}


def test_a_project_that_loosens_over_a_machine_that_is_a_person_s_asks(strict):
    asked, typed = said("")
    with pytest.raises(HumanRequired):
        loosening(["runtime.deploy"], typed=typed, project="alpha")
    assert authority.state_of("runtime.deploy", "alpha") == authority.PERSON and len(asked) == 1


def test_tightening_needs_no_person(strict):
    loosening(["ALL"], typed=said("yes")[1])
    asked, typed = said()
    authority.set_state(["ALL"], authority.PERSON, by="agent-lead01",
                        asking=authority.Asking(terminal=(False, False), env={"CLAUDECODE": "1"}, typed=typed))
    assert set(states().values()) == {authority.PERSON} and not asked


def cli(action, *words, project=""):
    return argparse.Namespace(action=action, words=list(words), project=project)


def test_a_lead_agent_cannot_loosen_through_the_workspace_command(board, monkeypatch):
    authority_cli.run(cli("preset", "prod", project="alpha"), board.parent)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(person, "is_terminal", lambda stream: True)
    monkeypatch.setattr(person, "typed", lambda prompt, wait_s=0: "yes")
    for call in (cli("set", "delegated", "ALL", project="alpha"), cli("preset", "dev", project="alpha")):
        with pytest.raises(HumanRequired, match="for a person"):
            authority_cli.run(call, board.parent)
    assert authority.state_of("sentinel.quarantine", "alpha") == authority.PERSON
    assert Tasks(board.parent).mode()["mode"] == "strict"


def test_a_lead_agent_may_still_tighten_through_the_workspace_command(board, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    authority_cli.run(cli("set", "person", "sentinel"), board.parent)
    assert authority.state_of("sentinel.policy") == authority.PERSON

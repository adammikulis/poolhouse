"""A launch in a project the lead has no session on yet: the lead's session is made on that board and the launched agent's helper is made under it.

The node is real and so is git; no model is served. The lead is a native Claude Code session of its own board (registered with its model, as the hook
does), the fresh project is a repository the node got to know afterwards, and the command runs with the lead's environment.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from confined_kit import repository
from delegate_kit import agent_names, said, started

from poolhouse import harnessid
from poolhouse.board import credentials, place, session

pytest_plugins = ["node_kit", "delegate_kit"]


def _fresh_project(node, tmp_path: Path, name: str = "fresh") -> tuple[Path, str]:
    """A git repository the node does not hold, then registered the way a launch registers it; its board holds no session of the lead."""
    project = repository(tmp_path / name)
    return project, place.add(node.client, project)


def test_a_lead_launching_in_a_project_it_has_no_session_on_gets_its_own_session_there_and_the_helper_is_made_under_it(lead_node, tmp_path, monkeypatch):
    node, lead, _project = lead_node
    fresh, board = _fresh_project(node, tmp_path)
    assert board != node.board and not (node.state / "client" / board / f"{lead.name}.token").exists()
    monkeypatch.setenv(session.AGENT_ENV, lead.name)
    monkeypatch.chdir(fresh)
    made = harnessid.invite("local-child", fresh, "", lambda _line: None)
    assert made.board == board and made.parent.name == lead.name
    assert credentials.load(node.state, board, lead.name)
    held = {a.name: a for a in made.parent.agents()}
    assert held[lead.name].parent == "" and held[made.name].parent == lead.name
    assert held[lead.name].model == "claude-sonnet-5-5"


def test_a_subagent_launching_in_a_project_the_lead_has_no_session_on_is_refused_at_once_and_makes_no_session(lead_node, tmp_path, monkeypatch):
    node, lead, _project = lead_node
    fresh, board = _fresh_project(node, tmp_path)
    monkeypatch.setenv(session.AGENT_ENV, lead.name)
    monkeypatch.setenv(session.SUBAGENT_ENV, "a-subagent")
    monkeypatch.chdir(fresh)
    said_lines: list[str] = []
    with pytest.raises(ValueError, match="could not be made"):
        harnessid.invite("local-child", fresh, "", said_lines.append)
    assert said_lines[0].startswith("workspace connection refused")
    assert not (node.state / "client" / board).exists() or not any((node.state / "client" / board).glob("*.token"))


def test_a_caller_that_names_no_session_and_runs_in_none_is_still_refused_in_a_project_it_has_no_session_on(lead_node, tmp_path, monkeypatch):
    node, _lead, _project = lead_node
    fresh, _board = _fresh_project(node, tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    monkeypatch.chdir(fresh)
    with pytest.raises(ValueError, match="started by a session"):
        harnessid.invite("local-child", fresh, "", lambda _line: None)


def test_a_delegate_started_in_a_project_the_lead_has_no_session_on_is_made_under_the_leads_session_there(lead_node, detached, tmp_path, monkeypatch, capsys):
    node, lead, _project = lead_node
    fresh, board = _fresh_project(node, tmp_path)
    monkeypatch.setenv(session.AGENT_ENV, lead.name)
    monkeypatch.chdir(fresh)
    assert started("--task", "list the python files here") == 0
    here = session.Session(node.client, board, credentials.load(node.state, board, lead.name), lead.name)
    [child] = [a.name for a in here.agents() if a.parent == lead.name]
    assert child in said(capsys) and agent_names(node, lead) == []


def test_the_leads_session_on_the_new_board_is_the_name_its_native_session_has_everywhere(lead_node, tmp_path, monkeypatch):
    node, lead, _project = lead_node
    fresh, board = _fresh_project(node, tmp_path)
    monkeypatch.setenv(session.AGENT_ENV, lead.name)
    assert harnessid.launcher(node.client, board, "").name == lead.name
    assert fresh.is_dir()

"""A session the harness still runs never stays without a token for want of a person.

A Claude Code session is registered once, at its SessionStart. Its token is a file that the SessionEnd hook deletes (and the node
revokes) whenever the session ends, and a session that ends and is resumed under the same native id, or that the node retired idle
overnight, goes on with the environment still naming it and no token. A command of that session registers its native session id
again and goes on, and no refusal sends the owner to a terminal. A subagent has no native main session to register.
"""

from __future__ import annotations

import pytest

from poolhouse import authority, node_launch, person, runtime_trust
from poolhouse.board import client as board_client, credentials, session as board_session

pytest_plugins = ["node_kit"]


def environment(member, **more: str) -> dict[str, str]:
    """What a Claude Code session's commands carry: the name its SessionStart exported and the native id the harness sets."""
    return {"POOLHOUSE_WORKSPACE_AGENT": member.name, "POOLHOUSE_SESSION_ID": member.session, "POOLHOUSE_SESSION_HARNESS": "claude-code", **more}


def token_file(node, member):
    return node.state / "client" / node.board / f"{member.name}.token"


def test_a_session_with_no_token_file_registers_again_from_its_native_id_and_goes_on(workspace_node):
    lead = workspace_node.member("lead")
    credentials.forget(workspace_node.state, workspace_node.board, lead.name)
    assert not token_file(workspace_node, lead).exists()
    done = workspace_node.cli("whoami", env=environment(lead))
    assert done.returncode == 0, done.stderr
    assert f"id: {lead.name}" in done.stdout, "the session keeps its name"
    assert token_file(workspace_node, lead).is_file(), "and has a token again, kept where the next command finds it"


def test_a_session_whose_token_the_node_no_longer_knows_gets_another_and_the_command_goes_on(workspace_node):
    lead = workspace_node.member("lead")
    workspace_node.session(lead).retire()
    with pytest.raises(board_client.Denied, match="token is not known"):
        workspace_node.session(lead).whoami()
    done = workspace_node.cli("whoami", env=environment(lead))
    assert done.returncode == 0, done.stderr
    assert credentials.load(workspace_node.state, workspace_node.board, lead.name) != lead.token
    assert workspace_node.session(workspace_node.adopt(lead.name)).whoami().name == lead.name


def test_a_command_of_a_session_the_board_never_named_registers_it(workspace_node):
    fresh = board_session.Native("", "claude-code", "never-started")
    done = workspace_node.cli("whoami", env={"POOLHOUSE_SESSION_ID": fresh.id, "POOLHOUSE_SESSION_HARNESS": "claude-code"})
    assert done.returncode == 0, done.stderr
    assert board_session.find("claude-code", fresh.id, client=workspace_node.client)


def test_a_subagent_has_no_native_main_session_to_register_and_stays_without_a_token(workspace_node):
    lead = workspace_node.member("lead")
    child = workspace_node.member("child", parent=lead)
    credentials.forget(workspace_node.state, workspace_node.board, child.name)
    done = workspace_node.cli("whoami", env={"POOLHOUSE_SUBAGENT": child.session})
    assert done.returncode != 0 and not token_file(workspace_node, child).exists()
    assert board_session.find("claude-code", child.session, client=workspace_node.client) == child.name, "it is still the lead's child"


def test_the_lead_whose_token_is_gone_is_authenticated_again_for_a_delegated_command(workspace_node, monkeypatch):
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    lead = workspace_node.member("lead")
    for name in (*person.AGENT_MARKERS, authority.AGENT_ENV, authority.SUBAGENT_ENV, "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("POOLHOUSE_BOARD", workspace_node.board)
    monkeypatch.setenv("POOLHOUSE_SESSION_ID", lead.session)
    monkeypatch.setenv("POOLHOUSE_SESSION_HARNESS", "claude-code")
    monkeypatch.setenv(authority.AGENT_ENV, lead.name)
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    credentials.forget(workspace_node.state, workspace_node.board, lead.name)
    assert runtime_trust.authorize("ensure") == authority.DELEGATED
    assert authority.current_agent().name == lead.name

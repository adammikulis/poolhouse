"""Delegated commands authenticate their existing native caller without identity exports."""

from __future__ import annotations

import os

import pytest

from poolhouse import authority, caller, node_launch
from poolhouse.board import client, session as sessions
from poolhouse.caller import AgentRefused
from poolhouse.session_identity import Unauthenticated

pytest_plugins = ["node_kit"]


@pytest.fixture
def native(workspace_node, monkeypatch):
    node = workspace_node
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    for name in (*caller.AGENT_MARKERS, authority.AGENT_ENV, authority.SUBAGENT_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POOLHOUSE_BOARD", node.board)
    monkeypatch.setattr(node_launch, "default_state", lambda: node.state)
    actor = node.member("native-root", harness="codex")
    monkeypatch.setenv("CODEX_THREAD_ID", actor.session)
    return node, actor


def test_registered_native_root_needs_no_marker_or_workspace_name(native):
    node, actor = native
    assert not os.environ.get(authority.AGENT_ENV)
    assert not os.environ.get("POOLHOUSE_AGENT")
    assert authority.require("workspace.setup", "install hooks") == authority.DELEGATED
    caller = authority.current_agent()
    assert caller.name == actor.name
    assert caller.identity.parent == ""
    assert caller.session.client.autostart is False
    assert authority.audit_rows()[-1]["agent"] == actor.name
    assert len(node.session(actor).agents()) == 1


def test_child_cannot_inherit_parent_label_or_forge_local_parent_record(native, monkeypatch):
    node, parent = native
    child = node.member("native-child", harness="codex", parent=parent)
    monkeypatch.setenv("CODEX_THREAD_ID", child.session)
    (node.state / "client" / node.board / f"{child.name}.parent").write_text("\n")
    with pytest.raises(Unauthenticated, match="live main"):
        authority.current_agent()
    monkeypatch.setenv(authority.AGENT_ENV, parent.name)
    with pytest.raises(Unauthenticated, match="authenticate"):
        authority.current_agent()


def test_unknown_native_never_uses_named_parent_or_registers(native, monkeypatch):
    node, parent = native
    monkeypatch.setenv("CODEX_THREAD_ID", "unknown-native")
    monkeypatch.setenv(authority.AGENT_ENV, parent.name)
    with pytest.raises(Unauthenticated, match="authenticate"):
        authority.current_agent()
    assert len(node.session(parent).agents()) == 1


def test_retired_native_does_not_revive(native):
    node, actor = native
    observer = node.member("observer")
    node.session(actor).retire()
    with pytest.raises(Unauthenticated, match="authenticate"):
        authority.current_agent()
    assert next(who for who in node.session(observer).agents(retired=True) if who.name == actor.name).retired


def test_native_context_cannot_be_hidden_by_an_explicit_empty_environment(native, monkeypatch):
    assert caller.marked({}) == "CODEX_THREAD_ID"
    monkeypatch.setenv(authority.FLOOR_ENV, "person")
    with pytest.raises(AgentRefused, match="held back from agents"):
        authority.require("workspace.setup", "install hooks", {})
    assert "CODEX_THREAD_ID" in [row for row in authority.audit_rows() if row["event"] == "refused"][-1]["who"]


def test_supplied_environment_does_not_select_another_identity(native):
    _node, actor = native
    assert authority.require("workspace.setup", "install hooks",
                             {"POOLHOUSE_AGENT": "1", authority.AGENT_ENV: "unrelated"}) == authority.DELEGATED
    used = [row for row in authority.audit_rows() if row["event"] == "authority.use"][-1]
    assert used["agent"] == actor.name != "unrelated"
    assert authority.current_agent().name == actor.name


def test_unavailable_node_is_not_started_or_downgraded_to_person(native, monkeypatch):
    node, _actor = native
    node.stop()
    monkeypatch.setattr(node_launch, "ensure_node", lambda *_a, **_k: pytest.fail("started the daemon"))
    with pytest.raises(Unauthenticated, match="authenticate"):
        authority.current_agent(client=client.Client(node.state))


def _refusal(**kwargs) -> str:
    with pytest.raises(Unauthenticated) as refused:
        authority.current_agent(**kwargs)
    return str(refused.value)


def test_a_down_node_is_named_with_the_command_to_run_next(native):
    node, actor = native
    node.stop()
    said = _refusal(client=client.Client(node.state))
    assert "node did not answer" in said and "poolhouse-workspace doctor" in said
    assert actor.token not in said


def test_an_unregistered_session_names_the_step_that_failed(native, monkeypatch):
    _node, actor = native
    monkeypatch.setenv("CODEX_THREAD_ID", "unknown-native")
    said = _refusal()
    assert "looking up this session" in said and "poolhouse-workspace doctor" in said
    assert actor.token not in said and "unknown-native" not in said


def test_a_token_file_that_is_not_private_names_the_file_not_its_content(native):
    node, actor = native
    (node.state / "client" / node.board / f"{actor.name}.token").chmod(0o644)
    said = _refusal()
    assert "reading the session token" in said and "not a private regular file" in said
    assert "chmod 600" in said and actor.token not in said


def test_conflicting_selectors_name_the_variables_to_unset(native, monkeypatch):
    _node, actor = native
    monkeypatch.setenv("POOLHOUSE_SESSION_HARNESS", "claude-code")
    monkeypatch.setenv("POOLHOUSE_SESSION_ID", actor.session)
    said = _refusal()
    assert "CODEX_THREAD_ID" in said and "POOLHOUSE_SESSION_ID" in said and "keep only" in said


def test_a_helper_is_told_whose_session_it_is_and_what_to_do(native, monkeypatch):
    node, parent = native
    child = node.member("native-child", harness="codex", parent=parent)
    monkeypatch.setenv("CODEX_THREAD_ID", child.session)
    said = _refusal()
    assert "live main agent session" in said
    assert child.name in said and parent.name in said and "poolhouse-workspace approval request" in said
    assert child.token not in said and parent.token not in said


def test_a_retired_session_says_it_is_retired(native):
    node, actor = native
    node.session(actor).retire()
    said = _refusal()
    assert "retired" in said and "poolhouse-workspace doctor" in said


def test_admission_handle_cannot_mutate_the_node_or_expose_its_token(native):
    node, actor = native
    caller = authority.current_agent()
    assert actor.token not in repr(caller)
    with pytest.raises(ValueError, match="read-only"):
        caller.session.call("retire")
    assert not node.session(actor).whoami().retired


def test_private_token_check_is_not_disabled_by_the_general_insecure_flag(native, monkeypatch):
    node, actor = native
    path = node.state / "client" / node.board / f"{actor.name}.token"
    path.chmod(0o644)
    monkeypatch.setenv("POOLHOUSE_ALLOW_INSECURE_CREDENTIALS", "yes-read-it-anyway")
    with pytest.raises(Unauthenticated, match="authenticate"):
        authority.current_agent()
    with pytest.raises(client.Denied):
        sessions.connect(client=node.client)


def test_command_and_admission_paths_refuse_conflicting_native_context(native, monkeypatch):
    node, actor = native
    monkeypatch.setenv("POOLHOUSE_SESSION_HARNESS", "claude-code")
    monkeypatch.setenv("POOLHOUSE_SESSION_ID", actor.session)
    with pytest.raises(Unauthenticated, match="authenticate"):
        authority.current_agent()
    with pytest.raises(client.Denied):
        sessions.connect(client=node.client)

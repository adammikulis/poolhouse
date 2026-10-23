"""Delegated commands authenticate their existing native caller without identity exports."""

from __future__ import annotations

import os

import pytest

from poolhouse import authority, node_launch, person
from poolhouse.board import client, session as sessions
from poolhouse.person import HumanRequired

pytest_plugins = ["node_kit"]


@pytest.fixture
def native(workspace_node, monkeypatch):
    node = workspace_node
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    for name in (*person.AGENT_MARKERS, authority.AGENT_ENV, authority.SUBAGENT_ENV):
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
    with pytest.raises(HumanRequired, match="live main"):
        authority.current_agent()
    monkeypatch.setenv(authority.AGENT_ENV, parent.name)
    with pytest.raises(HumanRequired, match="authenticate"):
        authority.current_agent()


def test_unknown_native_never_uses_named_parent_or_registers(native, monkeypatch):
    node, parent = native
    monkeypatch.setenv("CODEX_THREAD_ID", "unknown-native")
    monkeypatch.setenv(authority.AGENT_ENV, parent.name)
    with pytest.raises(HumanRequired, match="authenticate"):
        authority.current_agent()
    assert len(node.session(parent).agents()) == 1


def test_retired_native_does_not_revive(native):
    node, actor = native
    observer = node.member("observer")
    node.session(actor).retire()
    with pytest.raises(HumanRequired, match="authenticate"):
        authority.current_agent()
    assert next(who for who in node.session(observer).agents(retired=True) if who.name == actor.name).retired


def test_native_context_cannot_be_hidden_by_an_explicit_empty_environment(native, monkeypatch):
    assert person.marked({}) == "CODEX_THREAD_ID"
    monkeypatch.setenv(authority.FLOOR_ENV, "person")
    with pytest.raises(HumanRequired, match="for a person"):
        authority.require("workspace.setup", "install hooks", (True, True), {})


def test_supplied_environment_does_not_select_another_identity(native):
    _node, actor = native
    with pytest.raises(HumanRequired, match="conflicts"):
        authority.require("workspace.setup", "install hooks", (False, False),
                          {"POOLHOUSE_AGENT": "1", authority.AGENT_ENV: "unrelated"})
    assert authority.current_agent().name == actor.name


def test_unavailable_node_is_not_started_or_downgraded_to_person(native, monkeypatch):
    node, _actor = native
    node.stop()
    monkeypatch.setattr(node_launch, "ensure_node", lambda *_a, **_k: pytest.fail("started the daemon"))
    with pytest.raises(HumanRequired, match="authenticate"):
        authority.current_agent(client=client.Client(node.state))


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
    with pytest.raises(HumanRequired, match="authenticate"):
        authority.current_agent()
    with pytest.raises(client.Denied):
        sessions.connect(client=node.client)


def test_command_and_admission_paths_refuse_conflicting_native_context(native, monkeypatch):
    node, actor = native
    monkeypatch.setenv("POOLHOUSE_SESSION_HARNESS", "claude-code")
    monkeypatch.setenv("POOLHOUSE_SESSION_ID", actor.session)
    with pytest.raises(HumanRequired, match="authenticate"):
        authority.current_agent()
    with pytest.raises(client.Denied):
        sessions.connect(client=node.client)

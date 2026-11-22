"""Durable graph receipts consume each authenticated coding action at most once."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest

from poolhouse import coding_actions, pi_tools
from poolhouse.fleet import conversation_graph
from poolhouse.fleet.conversation_settings import checked
from poolhouse.fleet.conversation_types import CodingState, Conversation, Message
from poolhouse.fleet.conversations import Conversations
from poolhouse.harnesspolicy import Decision

from .test_coding_workspace import private_snapshot_storage, workspace  # noqa: F401

IDENTITY = {"actor": "coding-worker", "pool": "test-pool", "device": "test-device", "native_session": "test-native-session"}
IMPLEMENTATION = "a" * 64


@pytest.fixture
def owner(workspace, tmp_path):  # noqa: F811
    store = Conversations(tmp_path / "coding-state")
    conversation = Conversation(id="coding-test", model="local-model", settings=checked({"harness": "pi", "project_id": "a" * 32}))
    with conversation_graph.opened(store.root) as graph, graph.transaction():
        conversation_graph.save(graph, conversation)
        conversation_graph.append(graph, conversation, Message("user", "Update the selected repository"), 0)
        graph.put_doc(f"coding:{conversation.id}", {"version": 1, "workspace": workspace.receipt()})
    expected = CodingState.from_conversation(conversation)
    intent = coding_actions.Intent(0, IDENTITY, workspace.receipt(), IMPLEMENTATION)
    execution = store.begin_execution(expected, intent, current=lambda: True)
    return store, execution, workspace


def action(scope, identifier="tool-1", *, decision=None, content="after"):
    return coding_actions.Action.prepare(identifier, "write", {"path": "code.txt", "content": content},
        scope.receipt(), decision or Decision("allow", "reversible", "ordinary selected repository write"))


@contextmanager
def edited(store, execution, update):
    with conversation_graph.opened(store.root) as graph, graph.transaction():
        key = f"coding:{execution.policy.conversation}"
        saved = graph.get_doc(key, {})
        update(saved)
        graph.put_doc(key, saved)
    yield


def test_execution_initialization_attaches_only_to_its_original_user_message(owner):
    store, execution, scope = owner
    intent = coding_actions.Intent(0, IDENTITY, scope.receipt(), IMPLEMENTATION)
    assert store.begin_execution(execution.policy, intent, current=lambda: True) == execution
    for changed in (replace(intent, identity={**IDENTITY, "device": "copied-device"}),
                    replace(intent, implementation="b" * 64)):
        with pytest.raises(ValueError, match="history changed"):
            store.begin_execution(execution.policy, changed, current=lambda: True)
    assert store.begin_execution(execution.policy, intent, current=lambda: True) == execution


def test_deleted_execution_receipt_does_not_reinitialize_from_the_user_message(owner):
    store, execution, scope = owner
    with edited(store, execution, lambda saved: saved.pop("executions")), pytest.raises(ValueError, match="history changed"):
        store.begin_execution(execution.policy, coding_actions.Intent(0, IDENTITY, scope.receipt(), IMPLEMENTATION), current=lambda: True)


def test_message_changes_or_deletion_interrupt_the_bound_run(owner):
    store, execution, scope = owner
    call = action(scope)
    with conversation_graph.opened(store.root) as graph, graph.transaction():
        graph.query("MATCH (m:Node {id:$id}) SET m.label=$text", {"id": f"message:{execution.policy.conversation}:0", "text": "different intent"})
    with pytest.raises(ValueError, match="execution changed"):
        store.reserve_action(execution, call, current=lambda: True)
    with conversation_graph.opened(store.root) as graph, graph.transaction():
        graph.query("MATCH (m:Node {id:$id}) DETACH DELETE m", {"id": f"message:{execution.policy.conversation}:0"})
    with pytest.raises(ValueError, match="message is missing"):
        store.reserve_action(execution, call, current=lambda: True)


def test_persisted_running_action_never_runs_again_after_disconnect(owner):
    store, execution, scope = owner
    call = action(scope)
    assert store.reserve_action(execution, call, current=lambda: True)["state"] == "intent"
    assert store.start_action(execution, call, current=lambda: True)
    restarted = Conversations(store.root)
    assert restarted.reserve_action(execution, call, current=lambda: True)["state"] == "running"
    assert not restarted.start_action(execution, call, current=lambda: True)
    changed = replace(call, arguments=coding_actions.canonical({"path": "code.txt", "content": "different"}))
    with pytest.raises(ValueError, match="different arguments"):
        restarted.reserve_action(execution, changed, current=lambda: True)
    assert not (scope.source / "after").exists()


def test_completed_result_and_private_workspace_commit_together(owner):
    store, execution, scope = owner
    call = action(scope)
    store.reserve_action(execution, call, current=lambda: True)
    assert store.start_action(execution, call, current=lambda: True)
    result = {"content": [{"type": "text", "text": "tool completed"}]}
    def execute(stage):
        (stage.physical / "code.txt").write_text("after")
        return result
    def guard(capture, revision):
        return store.action_guard(execution, call, scope.next_receipt(capture, revision), result, current=lambda: True)
    scope.run(execute, guard, writable=True)
    row = store.action_state(execution, call, current=lambda: True)
    assert row["state"] == "completed" and row["result"] == result
    assert row["workspace"] == store.workspace_state(execution.policy) == scope.receipt()
    assert (scope.master / "code.txt").read_text() == "after"
    assert (scope.source / "code.txt").read_text() == "before"
    assert not store.start_action(execution, call, current=lambda: True)
    assert store.recorded_action(execution, call.id, call.tool, {"path": "code.txt", "content": "after"}, current=lambda: True) == row
    with pytest.raises(ValueError, match="different arguments"):
        store.recorded_action(execution, call.id, call.tool, {"path": "code.txt", "content": "changed"}, current=lambda: True)


def test_revocation_after_graph_save_rolls_back_result_and_parent_workspace(owner, monkeypatch):
    store, execution, scope = owner
    call = action(scope)
    store.reserve_action(execution, call, current=lambda: True)
    store.start_action(execution, call, current=lambda: True)
    result = {"content": [{"type": "text", "text": "unpublished"}]}
    current = [True]
    from poolhouse.graph.store import GraphStore
    original = GraphStore.put_doc
    def put(graph, key, value):
        original(graph, key, value)
        if key == f"coding:{execution.policy.conversation}" and value.get("workspace", {}).get("revision") == 1:
            current[0] = False
    monkeypatch.setattr(GraphStore, "put_doc", put)
    def execute(stage):
        (stage.physical / "code.txt").write_text("unpublished")
        return result
    def guard(capture, revision):
        return store.action_guard(execution, call, scope.next_receipt(capture, revision), result, current=lambda: current[0])
    with pytest.raises(ValueError, match="changed"):
        scope.run(execute, guard, writable=True)
    assert scope.revision == 0 and (scope.master / "code.txt").read_text() == "before"
    assert store.workspace_state(execution.policy)["revision"] == 0
    assert store.action_state(execution, call, current=lambda: True)["state"] == "running"


def test_an_allowed_action_starts_once_and_takes_no_approval(owner):
    import inspect

    store, execution, scope = owner
    call = action(scope)
    store.reserve_action(execution, call, current=lambda: True)
    assert "approval" not in inspect.signature(store.start_action).parameters
    assert store.start_action(execution, call, current=lambda: True)
    assert not store.start_action(execution, call, current=lambda: True), "the intent is consumed once"
    alias = replace(call, id="invented-next-id")
    assert store.reserve_action(execution, alias, current=lambda: True)["state"] == "attached"
    assert not store.start_action(execution, alias, current=lambda: True)


def test_a_denied_action_never_starts_and_nothing_can_ask_for_it_to(owner):
    store, execution, scope = owner
    call = action(scope, decision=Decision("deny", "destructive", "the guarded profile runs no destructive call"))
    assert store.reserve_action(execution, call, current=lambda: True)["state"] == "denied"
    assert not store.start_action(execution, call, current=lambda: True)
    assert store.action_state(execution, call, current=lambda: True)["state"] == "denied"
    assert not hasattr(store, "action_request") and not hasattr(coding_actions, "approval_request")
    with pytest.raises(ValueError, match="Invalid coding action decision"):
        action(scope, decision=Decision("ask", "destructive", "confirm this exact write", "tool_call_destructive"))


def test_a_denied_action_blocks_argument_variants_until_another_explicit_user_message(owner):
    store, execution, scope = owner
    call = action(scope, decision=Decision("deny", "unsure", "the guarded profile runs no unsure call"))
    store.reserve_action(execution, call, current=lambda: True)
    same = replace(call, id="invented-id")
    assert store.reserve_action(execution, same, current=lambda: True)["state"] == "attached"
    changed = action(scope, "variant", content="changed")
    with pytest.raises(ValueError, match="new user message"):
        store.reserve_action(execution, changed, current=lambda: True)
    store.append(execution.policy.conversation, "user", "Use this newly approved approach")
    next_run = store.begin_execution(execution.policy, coding_actions.Intent(1, IDENTITY, scope.receipt(), IMPLEMENTATION), current=lambda: True)
    assert next_run.id != execution.id
    assert store.reserve_action(next_run, changed, current=lambda: True)["state"] == "intent"
    with pytest.raises(ValueError, match="execution changed"):
        store.start_action(execution, call, current=lambda: True)


@pytest.mark.parametrize("raw", [b'{"tool":"read","tool":"write"}', b'{"input":NaN}', b'{"input":1e309}', b'[]'])
def test_action_frames_reject_ambiguous_or_nonfinite_json(raw):
    with pytest.raises(ValueError):
        coding_actions.parsed(raw)


@pytest.mark.parametrize("change", ["policy", "current", "message"])
def test_a_revoked_policy_message_or_current_check_stops_an_allowed_action_before_it_starts(owner, change):
    store, execution, scope = owner
    call = action(scope)
    store.reserve_action(execution, call, current=lambda: True)
    if change == "policy":
        store.update(execution.policy.conversation, settings={"execution_mode": "plan"}, expected_revision=execution.policy.revision)
    elif change == "message":
        with conversation_graph.opened(store.root) as graph, graph.transaction():
            graph.query("MATCH (m:Node {id:$id}) SET m.label='changed intent'", {"id": f"message:{execution.policy.conversation}:0"})
    with pytest.raises(ValueError, match="changed"):
        store.start_action(execution, call, current=lambda: change != "current")
    if change == "current":
        assert store.action_state(execution, call, current=lambda: True)["state"] == "intent"


def test_corrupt_completed_receipt_is_not_returned_as_a_replay(owner):
    store, execution, scope = owner
    call = action(scope)
    store.reserve_action(execution, call, current=lambda: True)
    def corrupt(saved):
        row = saved["executions"][execution.id]["actions"][call.id]
        row.update(state="completed", result={"content": []}, result_digest="0" * 64, workspace=scope.receipt())
    with edited(store, execution, corrupt), pytest.raises(ValueError, match="completed coding result changed"):
        store.recorded_action(execution, call.id, call.tool, {"path": "code.txt", "content": "after"}, current=lambda: True)


@pytest.mark.parametrize("raw", [b'{"tool":"read","tool":"write","input":{}}', b'{"tool":"read","input":{"limit":NaN}}'])
def test_actual_pi_worker_entry_refuses_invalid_frames_before_dispatch(tmp_path, monkeypatch, capsys, raw):
    import io
    import sys

    configuration = tmp_path / "worker.json"
    configuration.write_text("{}")
    monkeypatch.setattr(sys, "argv", ["pi_tools", str(configuration)])
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw)))
    monkeypatch.setattr(pi_tools, "execute", lambda *args: pytest.fail("Invalid action dispatched"))
    assert pi_tools.run() == 0
    assert '"isError": true' in capsys.readouterr().out


def test_wire_frame_limit_does_not_shrink_existing_snapshot_metadata_limits():
    large = {"manifest": "x" * (coding_actions.MAX_FRAME + 1)}
    call = coding_actions.Action.prepare("large-receipt", "read", {"path": "code.txt"}, large, Decision("allow", "safe", "read selected file"))
    assert len(call.workspace) > coding_actions.MAX_FRAME
    with pytest.raises(ValueError, match="frame limit"):
        coding_actions.call_arguments("oversize-input", "write", {"path": "code.txt", "content": large["manifest"]})

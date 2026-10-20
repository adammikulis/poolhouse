"""Current coding policy gates bounded publication in the real conversation graph."""
from __future__ import annotations

import threading
from dataclasses import replace

import pytest

from poolhouse.fleet import conversation_graph as graph
from poolhouse.fleet.conversations import Conversations
from poolhouse.workspace import coding_turns


@pytest.fixture
def coding(tmp_path):
    store = Conversations(tmp_path / "chat")
    conversation = store.start("model-a", settings={"harness": "pi", "project_id": "a" * 32,
                                                    "permissions": "ask-dangerous"})
    return store, conversation, store.coding_state(conversation.id)


def test_raw_project_path_does_not_establish_registered_coding_scope(tmp_path):
    store = Conversations(tmp_path / "chat")
    conversation = store.start(settings={"harness": "pi", "project": str(tmp_path)})
    with pytest.raises(ValueError, match="registered project"):
        store.coding_state(conversation.id)


@pytest.mark.parametrize("field,value", [("model", "other"), ("permissions", "yolo"),
    ("execution_mode", "plan"), ("harness", "codex"), ("project_id", "b" * 32), ("context", 4096)])
def test_latest_saved_model_policy_and_resource_reject_stale_publication(coding, field, value):
    store, conversation, expected = coding
    update = {"model": value} if field == "model" else {"settings": {field: value}}
    store.update(conversation.id, expected_revision=0, **update)
    with pytest.raises(ValueError, match="session changed"), store.coding_guard(expected, current=lambda: True):
        pytest.fail("stale policy entered publication")


@pytest.mark.parametrize("revision", [True, False, -1, "0", 0.0])
def test_noninteger_or_negative_expected_revision_is_rejected(coding, revision):
    store, _, expected = coding
    with pytest.raises(ValueError, match="invalid coding"), store.coding_guard(replace(expected, revision=revision), current=lambda: True):
        pytest.fail("invalid revision entered publication")


def test_cid_is_part_of_binding_even_when_policy_matches(coding):
    store, conversation, expected = coding
    other = store.start(conversation.model, settings=conversation.settings)
    assert store.coding_state(other.id) != expected
    store.remove(conversation.id)
    with pytest.raises(ValueError, match="session changed"), store.coding_guard(expected, current=lambda: True):
        pytest.fail("removed session published")


@pytest.mark.parametrize("current", [False, 1, "yes", None])
def test_current_authority_must_be_literal_true(coding, current):
    store, _, expected = coding
    with pytest.raises(ValueError, match="session changed"), store.coding_guard(expected, current=lambda: current):
        pytest.fail("inactive identity published")


def test_exit_revocation_rolls_back_graph_receipt(coding):
    store, _, expected = coding
    checks = iter([True, False])
    with pytest.raises(ValueError, match="before publishing"), store.coding_guard(expected, current=lambda: next(checks)) as transaction:
        transaction.put_doc("coding-result", {"result": "should not persist"})
    with graph.opened(store.root) as transaction:
        assert transaction.get_doc("coding-result") is None


def test_retained_root_replacement_invalidates_publication(coding, tmp_path):
    store, _, expected = coding
    root = tmp_path / "project"
    root.mkdir()
    original = root.stat()
    root.rename(tmp_path / "displaced")
    root.mkdir()
    def current():
        return (root.stat().st_dev, root.stat().st_ino) == (original.st_dev, original.st_ino)
    with pytest.raises(ValueError, match="session changed"), store.coding_guard(expected, current=current):
        pytest.fail("replaced root published")


def test_concurrent_policy_change_cannot_cross_publication_guard(coding):
    store, conversation, expected = coding
    attempted, finished = threading.Event(), threading.Event()
    def update():
        attempted.set()
        store.update(conversation.id, expected_revision=0, settings={"execution_mode": "plan"})
        finished.set()
    thread = threading.Thread(target=update)
    with store.coding_guard(expected, current=lambda: True) as transaction:
        thread.start()
        assert attempted.wait(5)
        assert not finished.is_set()
        transaction.put_doc("coding-result", {"revision": 0})
    thread.join(5)
    assert finished.is_set()
    with pytest.raises(ValueError, match="session changed"), store.coding_guard(expected, current=lambda: True):
        pytest.fail("old revision was accepted again")


def test_turn_receipt_preserves_durable_workspace_and_tool_results(coding, monkeypatch):
    store, conversation, _ = coding
    retained = {"workspace": {"master": "parent-owned-snapshot"}, "tool_results": {"call-id": "immutable-result"}}
    with graph.opened(store.root) as transaction, transaction.transaction():
        transaction.put_doc(f"coding:{conversation.id}", retained)
    monkeypatch.setattr(coding_turns.coding, "launch_coding_agent", lambda *args, **kwargs: 0)
    manager = coding_turns.Manager(store)
    monkeypatch.setattr(manager, "_parent", lambda project: "test-parent")
    manager._run(coding_turns.Turn(conversation.id), conversation, "inspect the code")
    with graph.opened(store.root) as transaction:
        saved = transaction.get_doc(f"coding:{conversation.id}")
    assert {key: saved[key] for key in retained} == retained
    assert saved["state"] == "completed"

"""Coding settings stay isolated and current across real conversation transactions."""
from __future__ import annotations

import pytest

from poolhouse.fleet import conversation_graph
from poolhouse.fleet.conversations import Conversations


@pytest.fixture
def sessions(tmp_path):
    store = Conversations(tmp_path / "chat")
    first = store.start(model="model-a", settings={"harness": "pi", "project": str(tmp_path / "first"),
                                                  "permissions": "ask-dangerous"})
    second = store.start(model="model-a", settings={"harness": "pi", "project": str(tmp_path / "second"),
                                                   "permissions": "read-only", "execution_mode": "plan"})
    return store, first, second


def test_each_session_keeps_independent_scope_permissions_and_mode(sessions):
    store, first, second = sessions
    changed = store.update(first.id, settings={"permissions": "yolo", "execution_mode": "plan"}, expected_revision=0)
    again = Conversations(store.root)
    assert changed.policy_revision == 1
    assert again.get(first.id).settings["permissions"] == "yolo"
    other = again.get(second.id)
    assert other.policy_revision == 0 and other.settings == second.settings
    assert other.settings["project"] != changed.settings["project"]


@pytest.mark.parametrize("revision", [None, True, False, -1, 1, "0", 0.0])
def test_stale_or_coerced_revision_cannot_change_any_security_or_model_setting(sessions, revision):
    store, first, _ = sessions
    with pytest.raises(ValueError, match="session changed"):
        store.update(first.id, model="replacement", title="replacement",
                     settings={"permissions": "yolo", "execution_mode": "execute", "project": "/unselected"},
                     expected_revision=revision)
    assert store.get(first.id).public() == first.public()


def test_actual_change_increments_once_and_repeated_same_values_are_idempotent(sessions):
    store, first, _ = sessions
    changed = store.update(first.id, model="model-b", settings={"context": 4096}, expected_revision=0)
    assert changed.policy_revision == 1
    same = store.update(first.id, model="model-b", settings={"context": 4096}, expected_revision=1)
    assert same.policy_revision == 1
    with pytest.raises(ValueError, match="session changed"):
        store.update(first.id, settings={"permissions": "yolo"}, expected_revision=0)
    assert store.get(first.id).policy_revision == 1


def test_messages_and_titles_never_invalidate_current_coding_policy(sessions):
    store, first, _ = sessions
    store.append(first.id, "user", "Inspect only")
    store.update(first.id, title="Inspection")
    current = store.get(first.id)
    assert current.policy_revision == 0 and current.title == "Inspection"
    assert current.messages[0].content == "Inspect only"


def test_switching_regular_chat_into_coding_requires_current_revision(tmp_path):
    store = Conversations(tmp_path / "chat")
    chat = store.start()
    assert chat.settings["permissions"] == "read-only"
    with pytest.raises(ValueError, match="session changed"):
        store.update(chat.id, settings={"harness": "pi", "permissions": "yolo"})
    current = store.get(chat.id)
    assert current.settings["harness"] == "" and current.policy_revision == 0


@pytest.mark.parametrize("settings", [{"permissions": "allow"}, {"execution_mode": "auto"},
                                       {"permissions": True}, {"execution_mode": []}])
def test_unknown_policy_values_are_rejected_without_mutating_session(sessions, settings):
    store, first, _ = sessions
    with pytest.raises(ValueError, match="permissions or execution mode"):
        store.update(first.id, settings=settings, expected_revision=0)
    assert store.get(first.id).public() == first.public()


def test_old_records_are_refused_without_implicit_policy_adoption(sessions):
    store, first, _ = sessions
    with conversation_graph.opened(store.root) as graph, graph.transaction():
        graph.upsert_node({"id": conversation_graph.node_id(first.id), "kind": "conversation", "label": "old",
                           "attrs": {"version": 1, "created": first.created, "model": first.model,
                                     "settings": first.settings}})
    with pytest.raises(ValueError, match="unsupported"):
        store.get(first.id)


def test_guarded_ui_api_round_trips_revision_and_rejects_replayed_changes(tmp_path):
    from test_fleet_ui import Serving

    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.conversations = Conversations(tmp_path / "chat")
    try:
        status, made, _ = served.call("/ui/conversations", method="POST",
                                     body={"settings": {"harness": "pi", "permissions": "ask-dangerous"}})
        assert status == 201 and made["policy_revision"] == 0
        path = "/ui/conversations/" + made["id"]
        change = {"settings": {"execution_mode": "plan"}, "expected_revision": 0}
        status, current, _ = served.call(path, method="POST", body=change)
        assert status == 200 and current["policy_revision"] == 1
        assert current["settings"]["execution_mode"] == "plan"
        assert served.call(path, method="POST", body=change)[0] == 400
        assert served.call(path, method="POST", body={"policy_revision": 99})[0] == 400
        assert served.call(path, method="POST", body={"settings": {"permissions": "yolo"}}, ui_header=False)[0] == 403
        assert served.ui.conversations.get(made["id"]).public() == current
    finally:
        served.close()

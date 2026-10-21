"""Model requests use the existing once-only coding action owner."""
from __future__ import annotations

import hashlib
import json

import pytest

from poolhouse import coding_actions
from poolhouse.fleet.chat import Target
from poolhouse.fleet.sdk_chat import prepare_completion
from poolhouse.harnesspolicy import Decision

from .test_coding_actions import owner  # noqa: F401
from .test_coding_workspace import private_snapshot_storage, workspace  # noqa: F401


def prepared(text="Inspect this repository"):
    return prepare_completion(Target("local-model", "http://127.0.0.1:1"),
                              {"model": "local-model", "messages": [{"role": "user", "content": text}]})


def test_model_intent_binds_the_complete_prepared_request_without_a_large_graph_frame(owner):  # noqa: F811
    _store, _execution, scope = owner
    request = prepared("x" * (coding_actions.MAX_FRAME + 1))
    action = coding_actions.Action.completion("model-1", request, scope.receipt())
    envelope = json.loads(action.arguments)
    assert envelope == {"model": "local-model", "request_digest": request.digest,
                        "request_bytes": len(request.arguments_json.encode())}
    assert envelope["request_digest"] == hashlib.sha256(request.arguments_json.encode()).hexdigest()
    assert len(action.arguments) < 1024
    assert coding_actions.COMPLETION not in coding_actions.TOOLS


def test_raw_model_arguments_cannot_enter_the_stock_tool_factory_or_receipt_lookup(owner):  # noqa: F811
    store, execution, scope = owner
    request = prepared()
    with pytest.raises(ValueError, match="stock coding tool"):
        coding_actions.Action.prepare("model-1", coding_actions.COMPLETION, request, scope.receipt(),
                                      Decision("allow", "compute", "selected model"))
    with pytest.raises(ValueError, match="prepared completion"):
        store.recorded_action(execution, "model-1", coding_actions.COMPLETION, request.arguments, current=lambda: True)


@pytest.mark.parametrize("competing_model", [False, True])
def test_model_and_tool_actions_share_the_same_running_owner(owner, competing_model):  # noqa: F811
    store, execution, scope = owner
    request = prepared()
    action = coding_actions.Action.completion("model-1", request, scope.receipt())
    first = store.reserve_action(execution, action, current=lambda: True)
    assert store.reserve_action(execution, action, current=lambda: True) == first
    assert store.start_action(execution, action, current=lambda: True)
    assert not store.start_action(execution, action, current=lambda: True)
    assert store.recorded_action(execution, action.id, coding_actions.COMPLETION, request,
                                 current=lambda: True)["state"] == "running"
    changed = prepared("x" * len("Inspect this repository"))
    assert len(changed.arguments_json) == len(request.arguments_json)
    with pytest.raises(ValueError, match="different arguments"):
        store.recorded_action(execution, action.id, coding_actions.COMPLETION, changed, current=lambda: True)
    competing = (coding_actions.Action.completion("model-2", changed, scope.receipt()) if competing_model else
                 coding_actions.Action.prepare("tool-1", "read", {"path": "code.txt"}, scope.receipt(),
                                               Decision("allow", "safe", "inspect selected repository")))
    store.reserve_action(execution, competing, current=lambda: True)
    with pytest.raises(ValueError, match="Another coding action"):
        store.start_action(execution, competing, current=lambda: True)


def test_running_model_request_cannot_use_tool_workspace_publication(owner):  # noqa: F811
    store, execution, scope = owner
    action = coding_actions.Action.completion("model-1", prepared(), scope.receipt())
    store.reserve_action(execution, action, current=lambda: True)
    assert store.start_action(execution, action, current=lambda: True)
    original = store.action_state(execution, action, current=lambda: True)
    previous = scope.receipt()
    replacement = scope.next_receipt(scope.initial, scope.revision)
    with (pytest.raises(ValueError, match="Only a stock coding tool"),
          store.action_guard(execution, action, replacement, {"content": "model reply"}, current=lambda: True)):
        pytest.fail("A model request entered the workspace mutation guard")
    assert store.action_state(execution, action, current=lambda: True) == original
    assert store.workspace_state(execution.policy) == previous
    assert scope.receipt() == previous

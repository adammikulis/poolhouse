"""Ordinary tools use their role policy without workspace registration or ownership transactions."""

import pytest

from poolhouse import coding_policy, harness_claims, harnesshook, node_launch
from poolhouse.board import place
from poolhouse.board.client import Client


@pytest.fixture
def no_coordination(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("ordinary tool policy contacted project, identity or claim coordination")

    for method in ("conflict", "reserve", "resources", "session_of"):
        monkeypatch.setattr(harness_claims, method, forbidden)
    monkeypatch.setattr(place, "resolve", forbidden)
    monkeypatch.setattr(Client, "call", forbidden)
    monkeypatch.setattr(node_launch, "ensure_node", forbidden)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", "stale-session")
    monkeypatch.setenv("POOLHOUSE_BOARD", "unregistered-project")
    monkeypatch.setattr(harnesshook, "_ask", lambda *args, **kwargs: (True, "authorized test call"))


@pytest.mark.parametrize("tool,arguments", [
    ("Edit", {"file_path": "app.py", "old_string": "old", "new_string": "new"}),
    ("Write", {"file_path": "app.py", "content": "ordinary work"}),
    ("apply_patch", {"input": "*** Begin Patch\n*** Add File: app.py\n+value = 1\n*** End Patch"}),
    ("Bash", {"command": "touch app.py"}),
    ("Bash", {"command": "git add app.py"}),
    ("Bash", {"command": "git push origin HEAD:refs/heads/0.3dev"}),
])
def test_ordinary_mutation_and_push_do_not_require_workspace(no_coordination, tmp_path, tool, arguments):
    rail = harnesshook.Rail(coding_policy.Policy("ask-dangerous"), "own", [str(tmp_path)])
    result = harnesshook.pre({"tool_name": tool, "tool_input": arguments, "cwd": str(tmp_path)}, rail)
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.parametrize("tool,arguments,permissions", [
    ("Write", {"file_path": "app.py", "content": "denied by read-only role"}, "read-only"),
    ("Bash", {"command": "poolhouse-workspace inbox --agent other"}, "ask-dangerous"),
    ("Bash", {"command": "poolhouse-workspace inbox --agent own --token-file /tmp/other"}, "ask-dangerous"),
])
def test_independent_role_and_explicit_identity_refusals_still_apply(no_coordination, tmp_path, tool, arguments, permissions):
    rail = harnesshook.Rail(coding_policy.Policy(permissions), "own", [str(tmp_path)])
    result = harnesshook.pre({"tool_name": tool, "tool_input": arguments, "cwd": str(tmp_path)}, rail)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_destructive_call_still_requires_its_policy_approval(no_coordination, tmp_path, monkeypatch):
    monkeypatch.setattr(harnesshook, "_ask", lambda *args, **kwargs: (False, "declined"))
    rail = harnesshook.Rail(coding_policy.Policy("ask-dangerous"), "own", [str(tmp_path)])
    result = harnesshook.pre({"tool_name": "Bash", "tool_input": {"command": "rm -rf ordinary-data"},
                              "cwd": str(tmp_path)}, rail)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

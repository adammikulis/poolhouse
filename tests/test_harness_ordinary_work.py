"""Ordinary tools use their role policy without workspace registration or ownership transactions."""

import pytest

from poolhouse import coding_policy, harnesshook, node_launch
from poolhouse.board import place
from poolhouse.board.client import Client


@pytest.fixture
def no_coordination(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("ordinary tool policy contacted project, identity or claim coordination")

    monkeypatch.setattr(place, "resolve", forbidden)
    monkeypatch.setattr(Client, "call", forbidden)
    monkeypatch.setattr(node_launch, "ensure_node", forbidden)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", "stale-session")
    monkeypatch.setenv("POOLHOUSE_BOARD", "unregistered-project")


@pytest.mark.parametrize("tool,arguments,permissions", [
    ("Edit", {"file_path": "app.py", "old_string": "old", "new_string": "new"}, "guarded"),
    ("Write", {"file_path": "app.py", "content": "ordinary work"}, "guarded"),
    ("apply_patch", {"input": "*** Begin Patch\n*** Add File: app.py\n+value = 1\n*** End Patch"}, "guarded"),
    ("Bash", {"command": "touch app.py"}, "guarded"),
    ("Bash", {"command": "git add app.py"}, "guarded"),
    ("Bash", {"command": "git push origin HEAD:refs/heads/0.3dev"}, "yolo"),
])
def test_ordinary_mutation_and_push_do_not_require_workspace(no_coordination, tmp_path, tool, arguments, permissions):
    if tool == "Edit":
        (tmp_path / "app.py").write_text("old")
    rail = harnesshook.Rail(coding_policy.Policy(permissions), "own", [str(tmp_path)])
    result = harnesshook.pre({"tool_name": tool, "tool_input": arguments, "cwd": str(tmp_path)}, rail)
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


@pytest.mark.parametrize("tool,arguments,permissions", [
    ("Write", {"file_path": "app.py", "content": "denied by read-only role"}, "read-only"),
    ("Bash", {"command": "poolhouse-workspace inbox --agent other"}, "guarded"),
    ("Bash", {"command": "poolhouse-workspace inbox --agent own --token-file /tmp/other"}, "guarded"),
])
def test_independent_role_and_explicit_identity_refusals_still_apply(no_coordination, tmp_path, tool, arguments, permissions):
    rail = harnesshook.Rail(coding_policy.Policy(permissions), "own", [str(tmp_path)])
    result = harnesshook.pre({"tool_name": tool, "tool_input": arguments, "cwd": str(tmp_path)}, rail)
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_push_is_denied_at_once_where_the_profile_runs_no_destructive_call(no_coordination, tmp_path):
    call = {"tool_name": "Bash", "tool_input": {"command": "git push origin HEAD:refs/heads/0.3dev"}, "cwd": str(tmp_path)}
    answer = harnesshook.pre(call, harnesshook.Rail(coding_policy.Policy("guarded"), "own", [str(tmp_path)]))
    assert answer["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "sends commits to a remote" in answer["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_destructive_call_is_denied_at_once_by_its_policy_and_yolo_allows_it(no_coordination, tmp_path):
    call = {"tool_name": "Bash", "tool_input": {"command": "rm -rf ordinary-data"}, "cwd": str(tmp_path)}
    guarded = harnesshook.pre(call, harnesshook.Rail(coding_policy.Policy("guarded"), "own", [str(tmp_path)]))
    answer = guarded["hookSpecificOutput"]
    assert answer["permissionDecision"] == "deny"
    assert "the guarded profile runs no destructive call" in answer["permissionDecisionReason"]
    yolo = harnesshook.pre(call, harnesshook.Rail(coding_policy.Policy("yolo"), "own", [str(tmp_path)]))
    assert yolo["hookSpecificOutput"]["permissionDecision"] == "allow"

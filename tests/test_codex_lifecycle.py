"""Codex lifecycle hooks use native identities on a real scratch node."""
import argparse
import datetime
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse import codex_lifecycle as lifecycle, codex_native, node_health
from poolhouse.board import session

IDS = {name: str(uuid.uuid4()) for name in ("native-root", "native-child", "native-nested", "child", "new-child", "unregistered-child")}


def event(stage, identifier="native-root", **fields):
    fields = {key: IDS.get(value, value) for key, value in fields.items()}
    return dict(hook_event_name=stage, session_id=IDS.get(identifier, identifier), model="gpt-6.1-sol", **fields)


def header(home, identifier, parent, **extra):
    today = datetime.datetime.now(datetime.UTC).date()
    folder = home / "sessions" / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-test-{identifier}.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": identifier,
                    "source": {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}, **extra}}) + "\n")
    return path


@pytest.fixture
def node(workspace_node, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setenv("CODEX_THREAD_ID", IDS["native-root"])
    monkeypatch.setenv("POOLHOUSE_BOARD", workspace_node.board)
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    native_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(native_home))
    header(native_home, IDS["native-child"], IDS["native-root"])
    header(native_home, IDS["native-nested"], IDS["native-child"])
    header(native_home, IDS["child"], IDS["native-root"])
    return workspace_node


def test_root_child_nested_are_separate_authenticated_sessions(node):
    root_context = lifecycle.handle(event("SessionStart"), node=node.client)
    root_name = session.find("codex", IDS["native-root"], client=node.client)
    child_context = lifecycle.handle(event("SubagentStart", agent_id="native-child", parent_agent_id="native-root"), node=node.client)
    child_name = session.find("codex", IDS["native-child"], client=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-nested", parent_agent_id="native-child"), node=node.client)
    nested_name = session.find("codex", IDS["native-nested"], client=node.client)
    root = session.connect(client=node.client, board=node.board, agent=root_name)
    child = session.connect(client=node.client, board=node.board, agent=child_name)
    nested = session.connect(client=node.client, board=node.board, agent=nested_name)
    assert root.name != child.name != nested.name
    assert child.whoami().parent == root.name
    assert nested.whoami().parent == child.name
    assert child.name in child_context["hookSpecificOutput"]["additionalContext"]
    assert "inherited" in child_context["hookSpecificOutput"]["additionalContext"]
    assert root.name in root_context["hookSpecificOutput"]["additionalContext"]
    assert "token" not in json.dumps(child_context)


@pytest.mark.parametrize("fields", [{}, {"agent_id": "native-root"}, {"agent_id": "new-child"},
                                     {"agent_id": "new-child", "parent_agent_id": "new-child"}])
def test_missing_ambiguous_or_self_parentage_never_falls_back(node, fields):
    lifecycle.handle(event("SessionStart"), node=node.client)
    with pytest.raises(ValueError):
        lifecycle.handle(event("SubagentStart", **fields), node=node.client)
    assert session.find("codex", IDS["new-child"], client=node.client) == ""


def test_child_stop_retires_only_child_and_does_not_restore_ended_id(node):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="child", parent_agent_id="native-root"), node=node.client)
    lifecycle.handle(event("SubagentStop", agent_id="child"), node=node.client)
    with pytest.raises(Exception, match=r"ended|retired|session"):
        lifecycle.handle(event("SubagentStart", agent_id="child", parent_agent_id="native-root"), node=node.client)
    name = session.find("codex", IDS["native-root"], client=node.client)
    assert not session.connect(client=node.client, board=node.board, agent=name).whoami().retired


def test_assigned_launcher_identity_is_not_replaced_and_child_not_parent(node):
    assigned = node.member("launcher-native", harness="codex", model="gpt-6.1-sol")
    result = lifecycle.handle(event("SessionStart"), assigned.name, node=node.client)
    assert assigned.name in result["hookSpecificOutput"]["additionalContext"]
    assert session.find("codex", IDS["native-root"], client=node.client) == ""
    lifecycle.handle(event("SubagentStart", agent_id="child", parent_agent_id="native-root"), assigned.name, node=node.client)
    lifecycle.handle(event("SubagentStop", agent_id="child"), assigned.name, node=node.client)
    assert not node.session(assigned).whoami().retired


def test_unregistered_child_does_not_use_assigned_or_inherited_parent(node, monkeypatch):
    assigned = node.member("assigned", harness="codex")
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", assigned.name)
    with pytest.raises(Exception, match=r"native|session"):
        lifecycle.handle(event("PostToolUse", agent_id="unregistered-child"), assigned.name, node=node.client)


def test_stopped_daemon_warns_quickly_and_does_not_block_or_restart(node):
    node.stop()
    before = time.monotonic()
    done = subprocess.run([sys.executable, "-m", "poolhouse.workspace.cli", "codex-hook"],
                          input=json.dumps(event("PostToolUse")), text=True, capture_output=True,
                          env=node.env(), timeout=5, check=False)
    assert done.returncode == 0 and time.monotonic() - before < 5
    assert "workspace lifecycle:" in done.stderr
    assert not done.stdout
    assert node_health.node_health(node.state) is None


def test_warning_redacts_credentials_and_malformed_event_is_advisory(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key-for-test")
    lifecycle.warning("failed secret-key-for-test")
    assert "secret-key-for-test" not in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("[]"))
    assert lifecycle.run(argparse.Namespace(assigned="", completion=False, worker=False)) == 0
    assert "must be an object" in capsys.readouterr().err


def test_documented_child_event_uses_actual_header_parent(node):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-child"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-nested"), node=node.client)
    parent_name = session.find("codex", IDS["native-child"], client=node.client)
    nested_name = session.find("codex", IDS["native-nested"], client=node.client)
    assert session.connect(client=node.client, board=node.board, agent=nested_name).whoami().parent == parent_name


def test_spoofed_parent_field_and_native_header_id_are_refused(node):
    lifecycle.handle(event("SessionStart"), node=node.client)
    with pytest.raises(ValueError, match="conflicts"):
        lifecycle.handle(event("SubagentStart", agent_id="native-child", parent_agent_id="native-nested"), node=node.client)
    home = Path(__import__("os").environ["CODEX_HOME"])
    header(home, IDS["native-child"], IDS["native-root"], id=IDS["native-nested"])
    with pytest.raises(ValueError, match="does not match"):
        codex_native.parent(event("SubagentStart", agent_id="native-child"))


def test_native_header_symlink_or_other_writable_file_is_refused(node, tmp_path):
    home = Path(__import__("os").environ["CODEX_HOME"])
    path = header(home, IDS["native-child"], IDS["native-root"])
    path.chmod(0o666)
    with pytest.raises(ValueError, match="owned regular"):
        codex_native.parent(event("SubagentStart", agent_id="native-child"))
    path.unlink()
    target = tmp_path / "outside-header"
    target.write_text("{}")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="owned regular"):
        codex_native.parent(event("SubagentStart", agent_id="native-child"))


def test_child_notification_without_agent_id_uses_actual_child(node, monkeypatch):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-child"), node=node.client)
    child_name = session.find("codex", IDS["native-child"], client=node.client)
    monkeypatch.setenv("CODEX_THREAD_ID", IDS["native-child"])
    root_name = session.find("codex", IDS["native-root"], client=node.client)
    result = lifecycle.handle(event("PostToolUse"), root_name, node=node.client)
    assert root_name not in json.dumps(result)
    if result:
        assert child_name in result["hookSpecificOutput"]["additionalContext"]
    lifecycle.handle(event("SessionEnd"), root_name, node=node.client)
    assert not session.connect(client=node.client, board=node.board, agent=root_name).whoami().retired
    retired = {actor.name: actor.retired for actor in node.session(node.adopt(root_name)).agents(retired=True)}
    assert retired[child_name]


def test_ambiguous_notification_and_assigned_thread_disagreement_are_refused(node, monkeypatch):
    assigned = node.member("assigned", harness="codex")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    with pytest.raises(ValueError, match="ambiguous"):
        lifecycle.handle(event("PostToolUse"), assigned.name, node=node.client)
    with pytest.raises(ValueError, match="actual native root"):
        lifecycle.handle(event("SessionStart"), assigned.name, node=node.client)


def test_nested_ambient_notification_proves_native_parent_chain(node, monkeypatch):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-child"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-nested"), node=node.client)
    monkeypatch.setenv("CODEX_THREAD_ID", IDS["native-nested"])
    lifecycle.handle(event("PostToolUse"), node=node.client)


def test_stop_guard_uses_own_child_and_preserves_cleanup_check(node, monkeypatch):
    lifecycle.handle(event("SessionStart"), node=node.client)
    lifecycle.handle(event("SubagentStart", agent_id="native-child"), node=node.client)
    root_name = session.find("codex", IDS["native-root"], client=node.client)
    child_name = session.find("codex", IDS["native-child"], client=node.client)
    monkeypatch.setenv("CODEX_THREAD_ID", IDS["native-child"])
    checked = []
    monkeypatch.setattr(lifecycle.worktree_lifecycle, "require_clean", lambda base, owner, **kw: checked.append(owner))
    assert lifecycle.completion(event("Stop"), root_name, node=node.client) == {}
    assert checked == [child_name]


def test_completion_guard_failures_block_separately_from_advisory_notices(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("[]"))
    assert lifecycle.run(argparse.Namespace(assigned="", completion=True, worker=True)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["decision"] == "block"

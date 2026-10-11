"""A subagent is its own board-named identity, and no caller-supplied field sets who is sending."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path

import pytest

from poolhouse import authority
from poolhouse.board import session as board_session
from poolhouse.board.client import Denied, NodeError
from poolhouse.sentinel.human import HumanRequired

pytest_plugins = ["node_kit"]
ROOT = Path(__file__).resolve().parents[1]
IDENTITY_FIELDS = ("sender", "name", "parent", "label", "from", "author", "actor", "identity", "as")
NOTE = {"type": "note", "to": "bob", "body": "hi", "subject": ""}


@pytest.fixture
def family(workspace_node):
    node = workspace_node
    node.lead = node.member("main-1")
    node.bob = node.member("bob")
    node.first = node.member("agent-a", parent=node.lead)
    node.second = node.member("agent-b", parent=node.lead)
    node.nested = node.member("agent-c", parent=node.first)
    return node


def refused(call, *args, **kwargs):
    with pytest.raises(NodeError) as caught:
        call(*args, **kwargs)
    return caught.value


@pytest.mark.redteam
def test_no_field_of_a_request_names_who_is_sending(family):
    node = family
    note = {**NOTE, "to": node.bob.name}
    for key in IDENTITY_FIELDS:
        for value in (node.lead.name, node.second.name):
            in_fields = refused(node.client.call, "post", node.board, node.first.token, kind="message", fields={**note, key: value})
            in_params = refused(node.client.call, "post", node.board, node.first.token, kind="message", fields=note, **{key: value})
            in_envelope = node.client._exchange({"v": 1, "id": 1, "method": "post", "board": node.board, "token": node.first.token,
                                                 "params": {"kind": "message", "fields": note}, key: value})
            assert in_fields.code == in_params.code == "denied", (key, value)
            assert in_envelope["ok"] is False and in_envelope["error"]["code"] in ("denied", "invalid"), (key, value)
    node.session(node.first).post(node.bob.name, "note", "real")
    assert [e.sender for e in node.session(node.bob).read(inbox=True).entries] == [node.first.name]


def test_the_session_api_has_no_parameter_that_names_a_sender():
    for method in (board_session.Session.post, board_session.Session.post_file, board_session.Session.note_add,
                   board_session.Session.claim, board_session.Session.release):
        assert not set(inspect.signature(method).parameters) & set(IDENTITY_FIELDS), method.__name__


@pytest.mark.redteam
def test_a_subagent_posts_as_itself_never_as_its_parent_or_another_subagent(family):
    node = family
    for child in (node.first, node.nested):
        assert node.cli("send", node.bob.name, "note", f"from {child.name}", who=child).returncode == 0
    senders = [e.sender for e in node.session(node.bob).read(inbox=True).entries]
    assert sorted(senders) == sorted([node.first.name, node.nested.name])
    assert node.lead.name not in senders and node.second.name not in senders


def test_a_main_session_and_two_subagents_one_nested_have_three_different_names(family):
    node = family
    assert len({node.lead.name, node.first.name, node.nested.name}) == 3
    assert all(name.startswith("claude-") for name in (node.first.name, node.nested.name))
    parents = {a.name: a.parent for a in node.session(node.lead).agents()}
    assert parents[node.first.name] == node.lead.name and parents[node.nested.name] == node.first.name
    assert parents[node.lead.name] == ""


def test_a_session_asking_for_a_subagent_of_another_gets_no_token(family):
    node = family
    again = board_session.Native("claude-sonnet-5-5", "claude-code", "agent-a")
    with pytest.raises(Denied):
        board_session.register(node.client, node.board, again, node.bob.token)
    assert board_session.register(node.client, node.board, again, node.lead.token).name == node.first.name


def test_a_delegated_gate_refuses_a_spawned_subagent_but_not_its_lead(family, monkeypatch):
    node = family
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    lead = {"CLAUDECODE": "1", "POOLHOUSE_WORKSPACE_AGENT": node.lead.name}
    assert authority.require("sentinel.policy", "mode", (False, False), lead) == authority.DELEGATED
    for helper in (node.first.name, node.nested.name, "../x", "A" * 3):
        with pytest.raises(HumanRequired, match="not a helper"):
            authority.require("sentinel.policy", "mode", (False, False), {**lead, "POOLHOUSE_WORKSPACE_AGENT": helper})


def test_the_hook_scripts_exist_without_a_label():
    done = subprocess.run(["grep", "-rn", "label", "scripts/hooks/claude-subagent-start",
                           "scripts/hooks/claude-subagent-stop", "scripts/hooks/workspace_hook.py"],
                          capture_output=True, text=True, cwd=str(ROOT), check=False)
    assert done.returncode == 1, done.stdout


def test_a_spawned_subagent_claims_every_kind_a_task_needs_and_its_parent_releases_them(family, tmp_path):
    node = family
    tree = tmp_path / "tree"
    tree.mkdir()
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    session = node.session(node.first)
    for kind, key in (("worktree", str(tree)), ("area", str(tree / "src")), ("branch", "feature/x"),
                      ("port", "9411"), ("server", "model-a")):
        assert session.claim(kind, key).owner == node.first.name
    held = [c for c in node.session(node.lead).claims() if c.owner == node.first.name]
    assert len(held) == 5
    taken = node.cli("claim", "branch", "feature/x", who=node.bob)
    assert taken.returncode == 5 and node.first.name in taken.stderr
    assert node.cli("release", "port", "9411", who=node.bob).returncode == 3
    assert node.cli("release", "port", "9411", "--held-by", node.first.name, who=node.lead).returncode == 0
    assert not [c for c in node.session(node.bob).claims() if c.key == "9411"]
    session.retire()
    assert [c for c in node.session(node.lead).claims() if c.owner == node.first.name] == []
    assert node.cli("claim", "branch", "feature/x", who=node.bob).returncode == 0

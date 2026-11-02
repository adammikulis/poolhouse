"""The readable name a session gets is made from the model it records, never from its harness or its own id."""
import json
import re

from poolhouse.workspace.agent_display import describe

pytest_plugins = ["node_kit"]
FAMILIES = [
    ("gpt-6", "codex", "chatgpt"),
    ("claude-sonnet-4-6", "claude-code", "claude"),
    ("Qwen3.8-Flash-Next-GSQ-RCO-Coder", "codex", "qwen"),
    ("thinkingcap-qwen3.8-27b", "codex", "qwen"),
    ("unknown-provider-model", "claude-code", "agent"),
]


def test_readable_family_uses_the_recorded_model_not_the_harness_or_the_native_id(workspace_node):
    node = workspace_node
    for number, (model, harness, expected) in enumerate(FAMILIES):
        member = node.member(f"codex-opaque-session-{number}", model=model, harness=harness)
        assert re.fullmatch(expected + r"-[0-9a-f]{6}", member.name), (model, harness, member.name)
        assert "codex-opaque-session" not in member.name
        known = {a.name: a for a in node.session(member).agents()}
        shown = describe(known[member.name])
        assert shown["display_name"] == member.name and shown["session_kind"] == "main"
        assert (known[member.name].model, known[member.name].harness) == (model, harness)


def test_the_name_of_a_session_stays_when_it_claims_another_model(workspace_node):
    node = workspace_node
    first, second = node.member("native-one", model="claude-sonnet-4-6"), node.member("native-two", model="claude-sonnet-4-6")
    assert first.name != second.name
    before = [a.name for a in node.session(first).agents()]
    done = node.cli("whoami", "--model", "Qwen3.8-Flash", "--harness", "codex", "--json", who=second)
    assert json.loads(done.stdout)["id"] == second.name
    assert [a.name for a in node.session(first).agents()] == before
    shown = {a["id"]: a for a in json.loads(node.cli("agents", "--json", who=first).stdout)}
    assert shown[second.name]["display_name"] == second.name and shown[second.name]["model"] == "Qwen3.8-Flash"

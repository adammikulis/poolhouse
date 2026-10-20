"""No model identifier decides a right: the listing names no coordinator and any claimed model id registers without restriction."""
import importlib.util
import json

import pytest

from poolhouse.workspace import landing
from poolhouse.workspace.agent_display import describe

pytest_plugins = ["node_kit"]
MODELS = ["claude-haiku-5-5", "gpt-6-luna", "qwen3.5-2b", "frontier-9000", "claude-sonnet-5-5", "claude-opus-5-5"]


def test_no_model_tier_table_exists():
    assert importlib.util.find_spec("poolhouse.workspace.model_tiers") is None


@pytest.mark.parametrize("model", MODELS)
def test_agents_lists_a_session_of_any_model_with_no_coordinator_marker(workspace_node, model):
    node = workspace_node
    lead = node.member("main-1", model=model)
    kid = node.member("kid", model=model, parent=lead)
    listed = json.loads(node.cli("agents", "--json", who=lead).stdout)
    assert all("coordinator" not in key for row in listed for key in row)
    assert {row["id"]: row["session_kind"] for row in listed} == {lead.name: "main", kid.name: "subagent"}
    assert {row["id"]: row["model"] for row in listed}[lead.name] == model
    text = node.cli("agents", who=lead).stdout
    assert "coordinator" not in text and lead.name in text and model in text


@pytest.mark.parametrize("model", MODELS)
def test_status_and_the_description_name_no_coordinator(workspace_node, model):
    node = workspace_node
    lead = node.member("main-1", model=model)
    shown = json.loads(node.cli("status", "--json", who=lead).stdout)
    assert not any("coordinator" in key for key in shown)
    assert all("coordinator" not in key for row in shown["registered"] for key in row)
    row = next(a for a in node.session(lead).agents() if a.name == lead.name)
    assert set(describe(row)) == {"display_name", "session_kind", "spawned_by"}


@pytest.mark.parametrize("model", MODELS)
def test_a_session_of_any_model_may_post_a_landing_review(workspace_node, model):
    node = workspace_node
    reviewer = node.member("reviewer", model=model)
    landing.review(node.session(reviewer), "feat/x", "a" * 40, "accept")
    found = landing.accepts(node.session(node.member("someone")), "a" * 40)
    assert [r["by"] for r in found["reviews"]] == [reviewer.name]

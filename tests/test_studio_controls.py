"""Conditional gates never dispatch an unselected branch."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from poolhouse.studio import contracts, engine as scheduler
from poolhouse.studio.store import StudioStore

PRINCIPAL = {"id": "session", "scope": "scope", "owner": True}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    original = contracts.node_spec
    def spec(kind, version=1):
        if kind != "test.guard":
            return original(kind, version)
        return {"type": kind, "version": 1, "parameters": {"properties": {}}, "capabilities": [],
                "inputs": [], "outputs": [{"name": name, "type": "gate"} for name in ("allow", "review", "block")],
                "implementation": "test-fixed-guard", "side_effects": True}
    monkeypatch.setattr(contracts, "node_spec", spec)
    monkeypatch.setattr(scheduler, "node_spec", spec)
    store = StudioStore(tmp_path / "studio.ladybug")
    calls = []
    class Adapters:
        outcome = "review"
        malformed = None
        def immediate(self, node, inputs, context):
            calls.append(node["id"])
            return {"prompt": node["parameters"]["template"]}
        def supports_authorized(self, node):
            return node["type"] == "test.guard"
        def execute_authorized(self, node, inputs, context):
            calls.append(node["id"])
            return self.malformed if self.malformed is not None else {
                name: {"allow": name == self.outcome, "reason": self.outcome}
                for name in ("allow", "review", "block")}
    adapters = Adapters()
    engine = scheduler.WorkflowEngine(store, SimpleNamespace(files_root=tmp_path / "files", jobs={}),
                                      adapters, lambda *args: True)
    engine.close()
    yield store, engine, adapters, calls
    store.close()


def graph():
    return {"nodes": [{"id": "guard", "type": "test.guard"},
                       *[{"id": name, "type": "prompt", "parameters": {"template": name}}
                         for name in ("target", "report", "other")]], "edges": [],
            "controls": [{"source": {"node": "guard", "port": "allow"}, "target": {"node": "target"}}]}


def run(setup, value, key="first", outputs=None):
    store, engine, _, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Controls", value)
    return engine.advance(engine.submit(PRINCIPAL, workflow, outputs or [], key))


def test_false_gate_skips_before_adapter_cache_or_artifact_access(setup):
    store, engine, adapters, calls = setup
    value = graph()
    adapters.outcome = "allow"
    first = run(setup, value)
    assert first["nodes"]["target"]["state"] == "done"
    calls.clear()
    adapters.outcome = "review"
    # Guard is not cached; the matching target cache must still never be read.
    second = run(setup, value, "second")
    assert second["state"] == "done"
    assert second["nodes"]["target"]["state"] == "skipped"
    assert "target" not in calls
    assert "fingerprint" not in second["nodes"]["target"]
    persisted = store.get("run", second["id"], PRINCIPAL["scope"])
    assert persisted["graph"]["controls"] == value["controls"]
    engine._recover()
    assert engine.advance(persisted)["nodes"]["target"]["state"] == "skipped"


def test_multiple_controls_are_conjunctive_and_skip_propagates(setup):
    value = graph()
    value["controls"].extend([
        {"source": {"node": "guard", "port": "review"}, "target": {"node": "target"}},
        {"source": {"node": "guard", "port": "allow"}, "target": {"node": "other"}},
        {"source": {"node": "guard", "port": "review"}, "target": {"node": "report"}}])
    result = run(setup, value)
    assert result["nodes"]["target"]["state"] == result["nodes"]["other"]["state"] == "skipped"
    assert result["nodes"]["report"]["state"] == "done"
    # A skipped data prerequisite propagates skip, preserving independent branches.
    result["graph"]["edges"].append({"source": {"node": "target", "port": "prompt"},
                                       "target": {"node": "other", "port": "unused"}})
    result["graph"]["controls"] = []
    result["nodes"]["other"] = {"state": "pending", "outputs": {}}
    assert setup[1].advance(result)["nodes"]["other"]["state"] == "skipped"


@pytest.mark.parametrize("bad", [None, {"allow": 1, "reason": "allow"}, {"allow": True, "reason": []},
                                  {"allow": True, "reason": "allow", "extra": True}])
def test_malformed_gate_never_dispatches(setup, bad):
    result = run(setup, graph())
    result["nodes"]["guard"].update(state="done", outputs={"allow": bad})
    result["nodes"]["target"] = {"state": "pending", "outputs": {}}
    setup[3].clear()
    assert setup[1].advance(result)["nodes"]["target"]["state"] == "blocked"
    assert "target" not in setup[3]


def test_guard_cannot_emit_multiple_active_gates(setup):
    setup[2].malformed = {name: {"allow": True, "reason": "allow"} for name in ("allow", "review", "block")}
    result = run(setup, graph())
    assert result["nodes"]["guard"]["state"] == "failed"
    assert result["nodes"]["target"]["state"] == "blocked"
    assert result["nodes"]["report"]["state"] == "done"


def test_selected_dependency_closure_keeps_control_source(setup):
    result = run(setup, graph(), outputs=["target"])
    assert {n["id"] for n in result["graph"]["nodes"]} == {"guard", "target"}
    assert result["nodes"]["target"]["state"] == "skipped"


def test_controls_validate_combined_cycle_unknown_ports_and_duplicates(setup):
    value = graph()
    value["controls"].append({"source": {"node": "target", "port": "prompt"}, "target": {"node": "guard"}})
    assert not contracts.validate_graph(value)["valid"]
    value["controls"][-1]["source"] = {"node": "guard", "port": "review"}
    assert any(e["code"] == "cycle" for e in contracts.validate_graph(value)["errors"])
    value = graph()
    value["controls"].append(deepcopy(value["controls"][0]))
    assert any(e["code"] == "control" for e in contracts.validate_graph(value)["errors"])


def test_group_and_sweep_controls_expand_without_losing_gates(setup):
    inner = graph()
    definition = {**inner, "outputs": {"gate": {"node": "guard", "port": "review"}}, "inputs": {}}
    value = {"nodes": [{"id": "group", "type": "group", "group": "saved"},
                       {"id": "outside", "type": "prompt", "parameters": {"template": "example"},
                        "sweep": {"template": ["one", "two"]}}],
             "edges": [], "groups": {"saved": definition},
             "controls": [{"source": {"node": "group", "port": "gate"}, "target": {"node": "outside"}}]}
    checked = contracts.validate_graph(value)
    assert checked["valid"], checked["errors"]
    assert len(checked["graph"]["controls"]) == 4
    assert all("/sweep/" in c["source"]["node"] for c in checked["graph"]["controls"])


def test_group_target_gates_every_descendant(setup):
    value = graph()
    members = [{"id": "one", "type": "prompt", "parameters": {"template": "one"}},
               {"id": "two", "type": "prompt", "parameters": {"template": "two"}}]
    value["nodes"].append({"id": "nested", "type": "group", "group": "saved"})
    value["groups"] = {"saved": {"nodes": members, "edges": []}}
    value["controls"] = [{"source": {"node": "guard", "port": "allow"}, "target": {"node": "nested"}}]
    result = run(setup, value)
    assert all(result["nodes"]["nested/" + name]["state"] == "skipped" for name in ("one", "two"))


def test_control_packages_roundtrip_and_reject_extra_authority(setup, monkeypatch):
    from poolhouse.studio import packages
    monkeypatch.setattr(packages, "node_spec", contracts.node_spec)
    value = graph()
    package = packages.export_package(value, package_id="guard-example", version="1", name="Guard example")
    assert packages.import_package(package)["graph"]["controls"] == value["controls"]
    package["graph"]["controls"][0]["grant"] = "forged"
    package["digest"] = packages.content_digest({k: v for k, v in package.items() if k != "digest"})
    with pytest.raises(ValueError, match="control"):
        packages.import_package(package)


def test_true_gate_still_checks_current_resource_authority(setup):
    _, engine, adapters, calls = setup
    adapters.outcome = "allow"
    engine.authorize = lambda principal, node, inputs: node is None or node["id"] != "target"
    # Submission is authorized; revoke after creating the immutable run snapshot.
    store = setup[0]
    workflow = store.save_workflow(PRINCIPAL["scope"], "Revocation", graph())
    engine.authorize = lambda *args: True
    result = engine.submit(PRINCIPAL, workflow, [], "revoked")
    engine.authorize = lambda principal, node, inputs: node is None or node["id"] != "target"
    result = engine.advance(result)
    assert result["nodes"]["target"]["state"] == "blocked"
    assert "target" not in calls


def test_adapter_can_refuse_incomplete_analysis_cache(setup):
    _, _engine, adapters, calls = setup
    run(setup, graph())
    calls.clear()
    adapters.cacheable_outputs = lambda node, outputs: False
    run(setup, graph(), "second")
    assert "report" in calls and "other" in calls
    calls.clear()
    run(setup, graph(), "third")
    assert "report" in calls and "other" in calls


def test_revoked_completed_control_dependency_blocks_only_its_branch(setup):
    store, engine, adapters, calls = setup
    adapters.outcome = "allow"
    workflow = store.save_workflow(PRINCIPAL["scope"], "Revoked guard", graph())
    result = engine.submit(PRINCIPAL, workflow, [], "control-revoked")
    result["nodes"]["guard"].update(state="done", outputs={
        name: {"allow": name == "allow", "reason": "allow"} for name in ("allow", "review", "block")})
    # The guard's terminal output is valid, but its authority was revoked before target dispatch.
    engine.authorize = lambda principal, node, inputs: node is None or node["id"] != "guard"
    result = engine.advance(result)
    assert result["nodes"]["target"]["state"] == "blocked"
    assert result["nodes"]["report"]["state"] == "done"
    assert "target" not in calls


def test_control_authorization_rechecks_transitive_data_resources(setup):
    result = run(setup, graph())
    result["graph"]["edges"] = [{"source": {"node": "other", "port": "prompt"},
                                   "target": {"node": "guard", "port": "data"}}]
    setup[1].authorize = lambda principal, node, inputs: node is None or node["id"] != "other"
    assert not setup[1]._control_authorized(result, "guard")


@pytest.mark.parametrize("uncertain", [False, True])
def test_revoked_control_cancels_running_child_and_preserves_unknown(setup, uncertain):
    store, engine, adapters, _calls = setup
    adapters.outcome = "allow"
    workflow = store.save_workflow(PRINCIPAL["scope"], "Running control", graph())
    result = engine.submit(PRINCIPAL, workflow, [], "running-revoked")
    result["nodes"]["guard"].update(state="done", outputs={
        name: {"allow": name == "allow", "reason": "allow"} for name in ("allow", "review", "block")})
    result["nodes"]["target"].update(state="running", job_id="owned-child")
    stopped = []
    def stop(state, principal):
        stopped.append(state["job_id"])
        if uncertain:
            raise OSError("lost cancellation acknowledgement")
    engine._stop_child = stop
    engine.authorize = lambda principal, node, inputs: node is None or node["id"] != "guard"
    result = engine.advance(result)
    assert stopped == ["owned-child"]
    assert result["nodes"]["target"]["state"] == ("interrupted" if uncertain else "cancelled")
    assert result["nodes"]["report"]["state"] == "done"

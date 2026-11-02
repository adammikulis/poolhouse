"""One durable finite run per authenticated service event, with active-only scheduling."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from poolhouse.studio import contracts, engine as scheduling
from poolhouse.studio.store import Conflict, StudioStore, digest

PRINCIPAL = {"id": "studio-grant:test", "kind": "studio_subscription", "grant_id": "test", "scope": "scope"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    original = contracts.node_spec
    def spec(kind, version=1):
        if kind != "stream.record":
            return original(kind, version)
        return {"type": kind, "version": 1, "inputs": [], "outputs": [{"name": "record", "type": "json"}],
                "parameters": {"properties": {"sample": {"type": "string", "default": "{}"}}},
                "capabilities": [], "implementation": "fixed-record"}
    monkeypatch.setattr(contracts, "node_spec", spec)
    monkeypatch.setattr(scheduling, "node_spec", spec)
    calls = []
    class Adapters:
        def immediate(self, node, inputs, context):
            calls.append((deepcopy(inputs), deepcopy(context["principal"])))
            return {"record": inputs.get("record", {})}
    store = StudioStore(tmp_path / "studio.ladybug")
    runner = SimpleNamespace(files_root=tmp_path / "files", jobs={})
    engine = scheduling.WorkflowEngine(store, runner, Adapters(), lambda *args: True)
    engine.close()
    workflow = store.save_workflow("scope", "Events", {"nodes": [{"id": "record", "type": "stream.record"}], "edges": []})
    yield store, engine, workflow, calls
    store.close()


def event():
    payload = {"message": "untrusted sample", "principal": {"owner": True}}
    return {"id": "event", "subscription": "subscription", "sequence": 1, "source": "source", "generation": "generation",
            "payload": payload, "digest": digest(payload), "input_node": "record", "output_nodes": ["record"]}


def test_event_payload_is_data_and_graph_configuration_remains_pinned(setup):
    store, engine, workflow, calls = setup
    value = event()
    run = engine.submit_event(PRINCIPAL, workflow, value)
    persisted = store.get("run", run["id"], "scope")
    assert persisted["event"] == value
    assert persisted["graph"]["nodes"][0]["parameters"]["sample"] == "{}"
    result = engine.advance(run)
    assert result["nodes"]["record"]["outputs"]["record"] == value["payload"]
    assert calls[0][1] == PRINCIPAL
    assert result["principal"] == PRINCIPAL


def test_duplicate_event_attaches_and_changed_bytes_conflict(setup):
    store, engine, workflow, calls = setup
    value = event()
    first = engine.submit_event(PRINCIPAL, workflow, value)
    assert engine.submit_event(PRINCIPAL, workflow, value)["id"] == first["id"]
    changed = deepcopy(value)
    changed["payload"]["message"] = "different"
    changed["digest"] = digest(changed["payload"])
    with pytest.raises(Conflict):
        engine.submit_event(PRINCIPAL, workflow, changed)
    assert len(store.records("run", "scope")) == 1
    assert calls == []


@pytest.mark.parametrize("mutation", ["principal", "digest", "payload", "input", "fields"])
def test_event_injection_rejects_forged_or_malformed_envelope(setup, mutation):
    store, engine, workflow, calls = setup
    value, principal = event(), deepcopy(PRINCIPAL)
    if mutation == "principal":
        principal.pop("kind")
    elif mutation == "digest":
        value["digest"] = "forged"
    elif mutation == "payload":
        value["payload"] = "not an object"
    elif mutation == "input":
        value["input_node"] = "missing"
    else:
        value["owner"] = True
    with pytest.raises((PermissionError, ValueError)):
        engine.submit_event(principal, workflow, value)
    assert store.records("run", "scope") == []
    assert calls == []


def test_run_event_identity_and_active_pointer_commit_atomically(setup, monkeypatch):
    store, engine, workflow, calls = setup
    write = store.graph.put_doc
    def fail(key, value):
        if key.startswith("studio.submission:"):
            raise OSError("crash at submission commit boundary")
        return write(key, value)
    with monkeypatch.context() as patch:
        patch.setattr(store.graph, "put_doc", fail)
        with pytest.raises(OSError):
            engine.submit_event(PRINCIPAL, workflow, event())
    assert store.records("run", "scope") == []
    assert store.tracked_runs() == []
    result = engine.submit_event(PRINCIPAL, workflow, event())
    assert result["event"] == event()
    assert len(store.tracked_runs()) == 1
    assert calls == []


def test_restart_keeps_exact_event_without_repeating_completed_output(setup):
    store, engine, workflow, calls = setup
    first = engine.advance(engine.submit_event(PRINCIPAL, workflow, event()))
    assert len(calls) == 1
    restarted = scheduling.WorkflowEngine(store, engine.runner, engine.adapters, lambda *args: True)
    restarted.close()
    again = restarted.submit_event(PRINCIPAL, workflow, event())
    assert again["id"] == first["id"] and again["state"] == "done"
    assert store.tracked_runs() == []
    assert len(calls) == 1


def test_completed_history_does_not_participate_in_poll_or_child_admission(setup, monkeypatch):
    store, engine, workflow, _ = setup
    complete = engine.advance(engine.submit_event(PRINCIPAL, workflow, event()))
    active = deepcopy(complete)
    active.update(id="active", state="running")
    active["nodes"]["record"].update(state="running", job_id="owned-child")
    store.put("run", active)
    reads = []
    original = store.graph.get_doc
    def observed(key, *args):
        reads.append(key)
        return original(key, *args)
    monkeypatch.setattr(store.graph, "get_doc", observed)
    def lookup():
        reads.clear()
        assert [run["id"] for run in engine._all_runs()] == ["active"]
        assert engine._admit_child(SimpleNamespace(id="owned-child"))
        return list(reads)
    before = lookup()
    for index in range(40):
        row = deepcopy(complete)
        row["id"] = "history-" + str(index)
        store.put("run", row)
    assert lookup() == before
    assert not any("history-" in key for key in reads)


def test_inconsistent_child_or_missing_active_index_never_dispatches(setup):
    store, engine, workflow, _ = setup
    run = engine.submit_event(PRINCIPAL, workflow, event())
    run["nodes"]["record"].update(state="running", job_id="owned-child")
    store.put("run", run)
    assert engine._admit_child(SimpleNamespace(id="owned-child"))
    store.graph.put_doc("studio.child:owned-child", {"run": run["id"], "node": "forged"})
    assert not engine._admit_child(SimpleNamespace(id="owned-child"))
    store.put("run", run)
    store.graph.query("MATCH (a:StudioRunActive {id:$id}) DELETE a", {"id": run["id"]})
    assert not engine._admit_child(SimpleNamespace(id="owned-child"))

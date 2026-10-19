"""Durable Studio snapshots, scheduling and uncertain execution recovery."""

from threading import Event
from types import SimpleNamespace

import pytest

from poolhouse.fleet.jobs import DaemonError, JobRunner
from poolhouse.studio.engine import WorkflowEngine, artifact_fingerprints
from poolhouse.studio.store import Conflict, StudioStore

PRINCIPAL = {"id": "session-test", "scope": "board-test", "owner": True}


def graph():
    return {"nodes": [{"id": "prompt", "type": "prompt", "version": 1,
                        "parameters": {"template": "example"}, "bindings": {}}], "edges": [], "groups": {}, "layout": {}}


class Adapters:
    def immediate(self, node, inputs, context):
        return {"prompt": node["parameters"]["template"]}


@pytest.fixture
def setup(tmp_path):
    store = StudioStore(tmp_path / "studio.ladybug")
    runner = SimpleNamespace(files_root=tmp_path / "files", jobs={})
    engine = WorkflowEngine(store, runner, Adapters(), lambda principal, node, inputs: True)
    engine.close()
    try:
        yield store, engine, runner
    finally:
        store.close()


def test_revision_conflicts_keep_copy_and_snapshot_is_immutable(setup):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Example", graph())
    run = engine.submit(PRINCIPAL, workflow, [], "first")
    changed = graph()
    changed["nodes"][0]["parameters"]["template"] = "edited"
    store.save_workflow(PRINCIPAL["scope"], "Edited", changed, ident=workflow["id"], revision=1)
    with pytest.raises(Conflict) as conflict:
        store.save_workflow(PRINCIPAL["scope"], "Other edit", changed, ident=workflow["id"], revision=1)
    assert conflict.value.copy["id"] != workflow["id"]
    assert store.get("run", run["id"], PRINCIPAL["scope"])["graph"]["nodes"][0]["parameters"]["template"] == "example"


def test_duplicate_submission_attaches_and_changed_graph_conflicts(setup):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Example", graph())
    first = engine.submit(PRINCIPAL, workflow, [], "submission")
    assert engine.submit(PRINCIPAL, workflow, [], "submission")["id"] == first["id"]
    workflow["graph"]["nodes"][0]["parameters"]["template"] = "changed"
    with pytest.raises(Conflict):
        engine.submit(PRINCIPAL, workflow, [], "submission")


def test_completed_pure_outputs_reuse_exact_fingerprint(setup):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Example", graph())
    first = engine.advance(engine.submit(PRINCIPAL, workflow, [], "one"))
    second = engine.advance(engine.submit(PRINCIPAL, workflow, [], "two"))
    assert first["state"] == second["state"] == "done"
    assert second["nodes"]["prompt"]["reused_from"] == first["id"]
    assert second["nodes"]["prompt"]["lineage"]["run"] == second["id"]


def test_revocation_blocks_after_predecessor_and_can_resume(setup):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Example", graph())
    run = engine.submit(PRINCIPAL, workflow, [], "one")
    engine.authorize = lambda principal, node, inputs: node is None
    run = engine.advance(run)
    assert run["nodes"]["prompt"]["state"] == "blocked"
    engine.authorize = lambda principal, node, inputs: True
    assert engine.advance(engine.resume(PRINCIPAL, run["id"]))["state"] == "done"


def test_unknown_running_child_never_replayed(setup):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Example", graph())
    run = engine.submit(PRINCIPAL, workflow, [], "one")
    run["nodes"]["prompt"].update(state="running", job_id="missing-child")
    store.put("run", run)
    engine._recover()
    recovered = store.get("run", run["id"], PRINCIPAL["scope"])
    assert recovered["nodes"]["prompt"]["state"] == "interrupted"
    with pytest.raises(ValueError, match="Reconcile"):
        engine.resume(PRINCIPAL, run["id"])


def test_cancel_is_idempotent_and_foreign_session_refused(setup):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Example", graph())
    run = engine.submit(PRINCIPAL, workflow, [], "one")
    with pytest.raises(PermissionError):
        engine.cancel({"id": "other", "scope": PRINCIPAL["scope"]}, run["id"])
    first = engine.cancel(PRINCIPAL, run["id"])
    assert first == engine.cancel(PRINCIPAL, run["id"])
    assert first["state"] == "cancelled"


def test_artifact_content_changes_and_symlink_escape_refused(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    file = data / "example.txt"
    file.write_text("first")
    before = artifact_fingerprints({"kind": "dataset", "path": str(data)})
    file.write_text("second")
    assert before != artifact_fingerprints({"kind": "dataset", "path": str(data)})
    (data / "escape").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="outside"):
        artifact_fingerprints({"kind": "dataset", "path": str(data)})


def test_job_submission_identity_attaches_without_requeue(tmp_path):
    runner = JobRunner(tmp_path, gate=lambda: (False, "test"))
    try:
        first = runner.submit("example", ["unused"], str(tmp_path), job_id="studio-test")
        assert runner.submit("example", ["unused"], str(tmp_path), job_id="studio-test") is first
        assert runner.status()["queued"] == 1
        with pytest.raises(DaemonError, match="different execution"):
            runner.submit("example", ["different"], str(tmp_path), job_id="studio-test")
        with pytest.raises(DaemonError, match="invalid job identity"):
            runner.submit("example", ["unused"], str(tmp_path), job_id="../escape")
    finally:
        runner.shutdown()


def test_queued_studio_child_waits_for_current_authorization_after_restart(tmp_path):
    runner = JobRunner(tmp_path, gate=lambda: (False, "test"))
    runner.submit("example", ["unused"], str(tmp_path), job_id="studio-restart")
    runner.checkpoint_restart()
    runner.shutdown()
    restored = JobRunner(tmp_path, gate=lambda: (False, "test"))
    try:
        assert restored.jobs["studio-restart"].state == "interrupted"
        assert restored.status()["queued"] == 0
        restored.reauthorize_studio("studio-restart")
        assert restored.status()["queued"] == 1
        with pytest.raises(DaemonError, match="uncertain"):
            restored.reauthorize_studio("studio-restart")
    finally:
        restored.shutdown()


def test_untyped_structured_response_paths_are_never_opened(tmp_path):
    assert artifact_fingerprints({"response": {"path": str(tmp_path / "missing")}}) == {}
    assert artifact_fingerprints({"path": str(tmp_path / "missing")}) == {}


def test_cache_is_principal_and_implementation_bound(setup):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Example", graph())
    engine.advance(engine.submit(PRINCIPAL, workflow, [], "first"))
    other = {**PRINCIPAL, "id": "other-session"}
    second = engine.advance(engine.submit(other, workflow, [], "second"))
    assert "reused_from" not in second["nodes"]["prompt"]
    engine.implementation = "different-worker-source"
    third = engine.advance(engine.submit(PRINCIPAL, workflow, [], "third"))
    assert "reused_from" not in third["nodes"]["prompt"]


def test_artifact_filename_and_content_have_distinct_fingerprints(tmp_path):
    folder = tmp_path / "artifacts"
    folder.mkdir()
    (folder / "a").write_text("bc")
    first = artifact_fingerprints({"kind": "artifact", "path": str(folder)})
    (folder / "a").unlink()
    (folder / "ab").write_text("c")
    assert first != artifact_fingerprints({"kind": "artifact", "path": str(folder)})


def test_queued_child_rechecks_authorization_at_actual_dispatch(tmp_path):
    checked = Event()
    runner = JobRunner(tmp_path)
    runner.studio_gate = lambda job: checked.set() or False
    try:
        runner.submit("example", ["unused"], str(tmp_path), job_id="studio-revoked")
        assert checked.wait(3)
        row = next(job for job in runner.snapshot() if job["id"] == "studio-revoked")
        assert row["state"] == "interrupted"
        assert row["pid"] is None
    finally:
        runner.shutdown()


def test_missing_dependency_prevents_any_durable_run_or_upstream_dispatch(setup):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Example", graph())
    engine.adapters.validate = lambda nodes: [{"node": "prompt", "code": "dependency", "message": "Missing optional library"}]
    with pytest.raises(ValueError, match="Missing optional library"):
        engine.submit(PRINCIPAL, workflow, [], "missing")
    assert store.records("run", PRINCIPAL["scope"]) == []


def test_unknown_selected_output_is_rejected(setup):
    store, engine, _ = setup
    workflow = store.save_workflow(PRINCIPAL["scope"], "Example", graph())
    with pytest.raises(ValueError, match="belong"):
        engine.submit(PRINCIPAL, workflow, ["missing"], "missing")


def test_missing_declared_output_fails_node_while_independent_branch_completes(setup):
    store, engine, _ = setup
    workflow_graph = graph()
    workflow_graph["nodes"].append({"id": "independent", "type": "prompt", "version": 1,
                                    "parameters": {"template": "other"}, "bindings": {}})
    workflow = store.save_workflow(PRINCIPAL["scope"], "Branches", workflow_graph)
    engine.adapters.immediate = lambda node, inputs, context: {} if node["id"] == "prompt" else {"prompt": "other"}
    run = engine.advance(engine.submit(PRINCIPAL, workflow, [], "branches"))
    assert run["nodes"]["prompt"]["state"] == "failed"
    assert run["nodes"]["independent"]["state"] == "done"


@pytest.mark.parametrize('kind', ['train.classify-text', 'train.text-lm', 'train.tool-calls', 'train.decision', 'gym.train'])
def test_typed_model_output_fingerprint_changes_invalidate_reuse(tmp_path, kind):
    from poolhouse.studio.engine import _artifacts
    model = tmp_path / 'model.safetensors'
    model.write_bytes(b'first weights')
    node = {'type': kind, 'version': 1}
    outputs = {'model': {'kind': 'model', 'path': str(model)}}
    first = _artifacts(node, outputs, 'outputs')
    assert first
    model.write_bytes(b'changed weights')
    assert first != _artifacts(node, outputs, 'outputs')

"""Durable Studio snapshots, scheduling and uncertain execution recovery."""

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

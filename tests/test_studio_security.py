"""Studio authorization boundaries through the daemon's HTTP interface."""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy

import pytest
from test_fleet_ui import Serving

from poolhouse.fleet.jobs import Job
from poolhouse.fleet.studio_service import scope


@pytest.fixture
def studio(tmp_path):
    served = Serving(tmp_path)
    served.ui.root = tmp_path / "traind"
    served.ui.settings.setup_done = True
    try:
        yield served
    finally:
        engine = getattr(served.ui, "studio", None)
        if engine is not None:
            engine.close()
            engine.store.close()
        served.close()


def _cookie(served, origin="token"):
    session = served.ui.sessions.open("studio-client", origin)
    return f"poolhouse_ui={session.sid}", session


def _workflow(served, cookie, graph=None):
    graph = graph or {"nodes": [{"id": "prompt", "type": "prompt", "version": 1,
                                "parameters": {"template": "A small experiment"}}],
                      "edges": [], "groups": {}, "layout": {}}
    status, body, _ = served.call("/ui/studio/workflows", method="POST", cookie=cookie,
                                  body={"title": "Experiment", "graph": graph})
    assert status == 201, body
    return body["workflow"]


def _run(served, cookie, workflow, key="submission-one"):
    status, body, _ = served.call("/ui/studio/runs", method="POST", cookie=cookie,
                                  body={"workflow_id": workflow["id"],
                                        "revision": workflow["revision"],
                                        "idempotency_key": key, "output_nodes": []})
    assert status == 202, body
    return body["run"]


@pytest.mark.parametrize("path", ["nodes", "templates", "workflows", "runs", "packages", "resources", "devices", "validate"])
def test_studio_requires_ui_header_and_rejects_remote_presence(studio, path):
    status, body, _ = studio.call(f"/ui/studio/{path}", ui_header=False)
    assert status == 403 and "header" in body["error"]
    status, _, _ = studio.call(f"/ui/studio/{path}", headers={"Host": "hostile.example"})
    assert status == 403
    assert not hasattr(studio.ui, "studio")


def test_submission_identity_is_scoped_to_current_execution_principal(studio):
    first, first_session = _cookie(studio)
    second, _ = _cookie(studio)
    workflow = _workflow(studio, first)
    run = _run(studio, first, workflow)
    assert _run(studio, first, workflow)["id"] == run["id"]
    for suffix in ("", "/artifacts"):
        status, _, _ = studio.call(f"/ui/studio/runs/{run['id']}{suffix}", cookie=second)
        assert status in {403, 404}
    status, _, _ = studio.call(f"/ui/studio/runs/{run['id']}/cancel", method="POST", cookie=second)
    assert status in {403, 404}
    persisted = json.dumps(studio.ui.studio.store.get("run", run["id"],
                                                     scope(studio.ui)))
    assert first_session.sid not in persisted
    studio.ui.sessions.close(first_session.sid)
    status, _, _ = studio.call(f"/ui/studio/runs/{run['id']}/artifacts", cookie=first)
    assert status in {401, 403, 404}


def test_workflow_revision_conflicts_preserve_both_edits(studio):
    cookie, _ = _cookie(studio, "launch-ticket")
    workflow = _workflow(studio, cookie)
    submitted = _run(studio, cookie, workflow)
    changed = {**workflow["graph"], "layout": {"prompt": {"x": 100, "y": 200}}}
    request = {"title": "Edited experiment", "graph": changed, "revision": workflow["revision"]}
    status, body, _ = studio.call(f"/ui/studio/workflows/{workflow['id']}", method="PUT",
                                  cookie=cookie, body=request)
    assert status == 200, body
    status, conflict, _ = studio.call(f"/ui/studio/workflows/{workflow['id']}", method="PUT",
                                      cookie=cookie, body={**request, "title": "Other edits"})
    assert status == 409, conflict
    recovered = conflict["conflict_copy"]
    assert recovered["id"] != workflow["id"] and recovered["graph"] == changed
    status, saved_run, _ = studio.call(f"/ui/studio/runs/{submitted['id']}", cookie=cookie)
    assert status == 200
    assert saved_run["run"]["revision"] == workflow["revision"]
    assert saved_run["run"]["graph"].get("layout", {}) != changed["layout"]


def test_imported_executable_content_never_submits_child_jobs(studio):
    cookie, _ = _cookie(studio)
    workflow = _workflow(studio, cookie)
    status, body, _ = studio.call("/ui/studio/packages/export", method="POST", cookie=cookie,
                                  body={"workflow_id": workflow["id"], "package_id": "example",
                                        "version": "1", "name": "Example", "description": "A shared prompt",
                                        "tags": ["examples"]})
    assert status == 200, body
    package = body["package"]
    assert package["description"] == "A shared prompt" and package["tags"] == ["examples"]
    package["graph"]["nodes"][0]["argv"] = ["python", "-c", "raise RuntimeError()"]
    from poolhouse.studio.packages import content_digest

    package["digest"] = content_digest({k: v for k, v in package.items() if k != "digest"})
    status, _, _ = studio.call("/ui/studio/packages/import", method="POST", cookie=cookie,
                               body={"package": package})
    assert status == 400
    assert studio.runner.snapshot() == []


def _report_run(served, cookie):
    original = _run(served, cookie, _workflow(served, cookie))
    engine = served.ui.studio
    original = engine.store.get("run", original["id"], engine._all_runs()[0]["scope"])
    original["id"] = uuid.uuid4().hex
    folder = served.files / "studio" / original["id"] / hashlib.sha256(b"report").hexdigest()[:24]
    folder.mkdir(parents=True)
    target = folder / "report.json"
    target.write_text('{"answer":42}')
    original.update(state="done", graph={"nodes": [{"id": "report", "type": "report", "version": 1}], "edges": []},
                    nodes={"report": {"state": "done", "fingerprint": "same-implementation-and-inputs",
                                      "outputs": {"artifact": {"kind": "artifact", "path": str(target)}}}})
    return engine.store.put("run", original)


def test_artifact_download_is_declared_streamed_and_rechecks_resource_access(studio):
    cookie, _ = _cookie(studio)
    run = _report_run(studio, cookie)
    status, listed, _ = studio.call(f"/ui/studio/runs/{run['id']}/artifacts", cookie=cookie)
    assert status == 200, listed
    file = listed["files"][0]
    assert file["name"] == "report.json"
    status, body, headers = studio.call(file["href"], cookie=cookie)
    assert status == 200 and body == {"answer": 42}
    assert headers["Content-Disposition"].startswith("attachment;")
    assert headers["X-Content-Type-Options"] == "nosniff"
    status, _, _ = studio.call(file["href"].replace("name=report.json", "name=..%2Fprivate.json"), cookie=cookie)
    assert status in {400, 404}
    studio.ui.studio_authorize = lambda principal, node, values: node is None
    assert studio.call(file["href"], cookie=cookie)[0] == 403
    assert studio.call(f"/ui/studio/runs/{run['id']}/artifacts", cookie=cookie)[0] == 403


def test_cached_artifacts_resolve_exact_authorized_original_without_copying(studio):
    cookie, _ = _cookie(studio)
    original = _report_run(studio, cookie)
    second = deepcopy(original)
    second["id"] = uuid.uuid4().hex
    second["nodes"]["report"]["reused_from"] = original["id"]
    studio.ui.studio.store.put("run", second)
    third = deepcopy(second)
    third["id"] = uuid.uuid4().hex
    third["nodes"]["report"]["reused_from"] = second["id"]
    studio.ui.studio.store.put("run", third)
    status, listed, _ = studio.call(f"/ui/studio/runs/{third['id']}/artifacts", cookie=cookie)
    assert status == 200, listed
    assert len(listed["files"]) == 1
    assert original["id"] in listed["files"][0]["href"]
    third["nodes"]["report"]["fingerprint"] = "substituted-implementation"
    studio.ui.studio.store.put("run", third)
    assert studio.call(f"/ui/studio/runs/{third['id']}/artifacts", cookie=cookie)[0] == 400


def test_run_logs_use_owned_child_identity_and_current_resource_authorization(studio):
    cookie, _ = _cookie(studio)
    run = _report_run(studio, cookie)
    job = Job("owned-child", "Report", ["fixed-worker"], str(studio.files), state="done")
    studio.runner.jobs[job.id] = job
    folder = studio.runner.job_dir(job.id)
    folder.mkdir(parents=True)
    studio.runner.log_path(job.id).write_text("Measured one result\n")
    run["nodes"]["report"]["job_id"] = job.id
    studio.ui.studio.store.put("run", run)
    route = f"/ui/studio/runs/{run['id']}/logs?node=report"
    status, body, _ = studio.call(route, cookie=cookie)
    assert status == 200 and body["log"] == "Measured one result\n"
    assert body["job"]["id"] == job.id
    assert studio.call(route.replace("node=report", "node=..%2Fprivate"), cookie=cookie)[0] == 404
    studio.ui.studio_authorize = lambda principal, node, values: node is None
    assert studio.call(route, cookie=cookie)[0] == 403

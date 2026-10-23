"""The RBMK control room's server: a real HTTP server on loopback, real plain-code runs, real documents.

No model runs here except in the tests marked real_model, which run the released decision model through the hub under the GPU
hold; the naive controller and the crew script are plain declared policy code.
"""

from __future__ import annotations

import copy
import http.client
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from rbmk_run import already_held, plain_run, sync_recording

from examples.rbmk import scenarios
from examples.rbmk.display import export
from examples.rbmk.web import recordings, server
from examples.rbmk.web.hub import Hub

pytest_plugins = ["real_model"]


@pytest.fixture
def served(tmp_path: Path):
    hub = Hub(tmp_path)
    srv = server.serve(hub, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield hub, srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def plain(tmp_path_factory):
    """A finished naive run on pump_trip as a run document."""
    return plain_run(tmp_path_factory.mktemp("rbmk-web"), "pump_trip", "naive")


def call(port: int, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    head = {"Content-Type": "application/json", **(headers or {})}
    data = body if isinstance(body, bytes) else (None if body is None else json.dumps(body).encode())
    conn.request(method, path, body=data, headers=head)
    res = conn.getresponse()
    raw = res.read()
    conn.close()
    kind = res.getheader("Content-Type", "")
    return res.status, (json.loads(raw) if kind.startswith("application/json") else raw.decode(errors="replace"))


def events(port: int, job: str, after: int = 0, headers: dict[str, str] | None = None) -> list[dict[str, Any]]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=120)
    conn.request("GET", f"/api/jobs/{job}/events?after={after}", headers=headers or {})
    text = conn.getresponse().read().decode()
    conn.close()
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def run_job(port: int, scenario: str, controller: str = "naive") -> tuple[str, list[dict[str, Any]]]:
    status, job = call(port, "POST", "/api/run", {"scenario": scenario, "controller": controller})
    assert status == 200, job
    return job["id"], events(port, job["id"])


def test_the_server_listens_on_loopback_only(served):
    _, port = served
    assert server.serve(Hub(Path("/nonexistent")), 0).server_address[0] == "127.0.0.1"
    status, page = call(port, "GET", "/")
    assert status == 200 and "<title>RBMK control room</title>" in page


@pytest.mark.parametrize("path", ["/room/main.js", "/room/room.glb.gz", "/room/instruments.json", "/room/stills/front.webp",
                                  "/room/stills/masks/front.png", "/assets/three.module.js", "/assets/three.core.js",
                                  "/assets/OrbitControls.js", "/assets/RoomEnvironment.js", "/js/app.js", "/css/app.css"])
def test_the_room_and_its_scripts_are_served_from_the_one_server(served, path):
    _, port = served
    assert call(port, "GET", path)[0] == 200


@pytest.mark.parametrize("path", ["/room/shoot.py", "/room/check.mjs", "/room/../web/server.py", "/room/%2e%2e/web/server.py", "/room/vendor",
                                  "/assets/agents.js", "/assets/poolhouse-ui.js", "/assets/../board.js", "/assets/%2e%2e/base.js", "/room/", "/room"])
def test_only_the_rooms_own_files_and_the_three_shared_scripts_are_served(served, path):
    _, port = served
    assert call(port, "GET", path)[0] == 404


@pytest.mark.parametrize("path", ["/../server.py", "/%2e%2e/server.py", "/js/../../hub.py", "/js/nothing.js", "/js", "//etc/passwd", "/api/nothing"])
def test_only_the_static_files_are_served(served, path):
    _, port = served
    assert call(port, "GET", path)[0] == 404


def test_a_page_from_another_origin_cannot_use_the_server(served):
    _, port = served
    assert call(port, "GET", "/api/info", headers={"Host": "evil.example"})[0] == 403
    assert call(port, "POST", "/api/run", {"scenario": "full_power", "controller": "naive"}, headers={"Origin": "http://evil.example"})[0] == 403
    assert call(port, "POST", "/api/run", b"{}", headers={"Content-Type": "text/plain"})[0] == 415


def test_bad_requests_are_refused_with_the_reason(served):
    hub, port = served
    assert call(port, "POST", "/api/run", b"not json")[0] == 400
    assert call(port, "POST", "/api/run", b"[]")[0] == 400
    status, body = call(port, "POST", "/api/run", {"scenario": "no_such", "controller": "naive"})
    assert status == 404 and "no_such" in body["error"]
    status, body = call(port, "POST", "/api/run", {"scenario": "full_power", "controller": "crew"})
    assert status == 400 and "1986" in body["error"]
    assert call(port, "POST", "/api/run", {"scenario": "full_power", "controller": "naive", "memory": "state"})[0] == 400
    assert call(port, "POST", "/api/run", {"scenario": "full_power", "controller": "naive", "decider": "nothing"})[0] == 400
    assert call(port, "POST", "/api/run", {"scenario": "full_power", "controller": "naive", "speed": 0})[0] == 400
    assert call(port, "POST", "/api/run", b"x" * (server.BODY_LIMIT + 10))[0] == 413
    assert hub.jobs == {}, "a refused request starts nothing"


def test_the_info_names_every_scenario_the_decider_and_the_memory_variants(served):
    _, port = served
    status, info = call(port, "GET", "/api/info")
    assert status == 200
    assert {s["key"] for s in info["scenarios"]} == set(scenarios.SCENARIOS)
    assert info["model"]["pinned"].startswith("strands-decider") and info["model"]["id"] == ""
    assert info["schema"] == export.SCHEMA and [d["id"] for d in info["deciders"]] == [""] and info["memories"] == ["none", "history"]
    assert 60.0 in info["speeds"]


def test_a_naive_run_streams_rows_decisions_and_a_document(served):
    _, port = served
    job, seen = run_job(port, "stuck_rod")
    kinds = [e["type"] for e in seen]
    assert kinds[0] == "header" and kinds[-1] == "status" and seen[-1]["status"] == "done"
    assert [e["seq"] for e in seen] == list(range(len(seen)))
    assert {"trace", "row", "document", "caption"} <= set(kinds)
    assert len(seen[0]["rods"]) == 211 and seen[0]["scenario"]["title"] == "Stuck rod" and seen[0]["controller"] == "naive"
    status, doc = call(port, "GET", f"/api/jobs/{job}/document")
    assert status == 200 and recordings.check(json.loads(json.dumps(doc)))["meta"]["controller"] == "naive"
    assert [e["row"] for e in seen if e["type"] == "trace"] == doc["trace"], "the streamed trace rows are the rows of the document"
    streamed = [e["row"] for e in seen if e["type"] == "row"]
    landed = {e["i"]: e for e in seen if e["type"] == "landed"}
    for row in streamed:
        if row["kind"] == "decision" and row["i"] in landed:
            row.update(applied_tick=landed[row["i"]]["applied_tick"], applied_t_s=landed[row["i"]]["applied_t_s"], log_line=landed[row["i"]]["log_line"])
    assert streamed == doc["rows"], "the streamed decision and event rows are the rows of the document"
    assert doc["captions"] == [e["row"] for e in seen if e["type"] == "caption"] or len(doc["captions"]) >= 1


def test_a_stream_can_resume_after_an_event(served):
    _, port = served
    job, seen = run_job(port, "full_power")
    later = [e["seq"] for e in seen[10:]]
    assert [e["seq"] for e in events(port, job, after=10)] == later
    assert [e["seq"] for e in events(port, job, after=0, headers={"Last-Event-ID": "9"})] == later
    assert events(port, job, after=len(seen)) == []


def test_the_crew_script_runs_only_on_the_1986_replay(served):
    _, port = served
    job, seen = run_job(port, "historic_1986", "crew")
    assert seen[-1]["status"] == "done"
    doc = call(port, "GET", f"/api/jobs/{job}/document")[1]
    assert doc["meta"]["controller"] == "crew" and recordings.label(doc).startswith("1986 crew script (not a model)")


def test_a_model_run_is_refused_at_once_when_the_model_is_busy(served):
    hub, port = served
    hub.slot.acquire()
    try:
        status, body = call(port, "POST", "/api/run", {"scenario": "full_power", "controller": "model"})
        assert status == 409 and "busy" in body["error"]
        assert hub.jobs == {}
    finally:
        hub.slot.release()


def test_a_run_document_carries_every_what_if_branch_without_asking_a_model(plain):
    requests = [r["n"] for r in plain["rows"] if r["kind"] == "decision"]
    assert sorted(map(int, plain["whatif"])) == requests and requests
    branch = plain["whatif"][str(requests[0])]
    assert set(branch) == set(plain["classes"]) and "hold" in branch and "scram" in branch
    assert all(len(b["rows"]) == 10 and "damaged" in b and "peak_P" in b for b in branch.values())


def test_a_well_formed_document_is_accepted_and_labelled(plain):
    doc = recordings.check(copy.deepcopy(plain))
    assert recordings.label(doc) == "naive controller (plain policy code, not a model)"
    row = recordings.summary("a.json", doc)
    assert row["scenario"] == "pump_trip" and row["controller"] == "naive" and row["kind"] == "run" and row["title"] == "Pump trip"


def _model(doc: dict[str, Any]) -> dict[str, Any]:
    doc["meta"]["controller"] = "model"
    doc["meta"]["backend"] = {"mode": "recorded", "model": "m-1", "recorded_at": "2026-01-02"}
    return doc


def test_a_model_run_names_its_model_and_the_date_in_its_label(plain):
    assert recordings.label(_model(copy.deepcopy(plain))) == "recorded from m-1 on 2026-01-02"
    doc = _model(copy.deepcopy(plain))
    del doc["meta"]["backend"]["recorded_at"]
    assert recordings.label(doc) == "recorded from m-1 on a date that was not stored"


@pytest.mark.parametrize("mutate,why", [
    (lambda d: d.update(schema="other/1"), "schema"),
    (lambda d: d["meta"]["backend"].update(model=""), "model id"),
    (lambda d: d["meta"].update(controller="oracle"), "controller"),
    (lambda d: d.pop("trace"), "trace"),
    (lambda d: d.update(trace=[]), "no rows"),
    (lambda d: d["trace"][0].pop("P"), "lacks a required field"),
    (lambda d: d["trace"][0].update(rods=[0, 1]), "rod positions"),
    (lambda d: d.update(rows=[{"tick": 0}]), "row 0"),
    (lambda d: d["meta"].pop("scenario"), "meta.scenario"),
])
def test_a_malformed_document_is_refused_with_the_reason(plain, mutate, why):
    doc = _model(copy.deepcopy(plain))
    mutate(doc)
    with pytest.raises(recordings.BadDocument, match=why):
        recordings.check(doc)


def test_text_that_is_not_json_or_not_an_object_is_refused():
    for text in (b"", b"{", b"[1]", b'"x"', b"\xff\xfe"):
        with pytest.raises(recordings.BadDocument):
            recordings.parse(text)


@pytest.mark.parametrize("name", ["../x.json", "a/../b.json", ".hidden.json", "x.txt", "", "a" * 200 + ".json", "x\n.json", "x.json\n", "/abs.json", "a//b.json"])
def test_run_store_names_are_plain_relative_file_paths(tmp_path, name):
    with pytest.raises((recordings.BadDocument, FileNotFoundError)):
        recordings.read(tmp_path, name)


def test_the_run_store_list_shows_files_that_cannot_be_shown(served, tmp_path, plain):
    port = served[1]
    (tmp_path / "m" / "none").mkdir(parents=True)
    (tmp_path / "m" / "none" / "good.json").write_text(json.dumps(plain))
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "old.json").write_text(json.dumps({**plain, "schema": "rbmk-run/1"}))
    status, rows = call(port, "GET", "/api/recordings")
    by_name = {r["name"]: r for r in rows}
    assert status == 200 and by_name["m/none/good.json"]["ok"] and not by_name["broken.json"]["ok"] and "JSON" in by_name["broken.json"]["error"]
    assert not by_name["old.json"]["ok"] and "schema" in by_name["old.json"]["error"]
    assert call(port, "GET", "/api/recording?name=broken.json")[0] == 422
    assert call(port, "GET", "/api/recording?name=missing.json")[0] == 404
    status, shown = call(port, "GET", "/api/recording?name=m/none/good.json")
    assert status == 200 and shown["doc"]["trace"] == plain["trace"]
    assert call(port, "GET", "/api/recording?name=../good.json")[0] in (404, 422)
    assert call(port, "POST", "/api/load", b"{")[0] == 422
    status, loaded = call(port, "POST", "/api/load", json.dumps(plain).encode())
    assert status == 200 and loaded["doc"]["outcome"] == plain["outcome"]
    assert call(port, "GET", "/api/summary") == (200, {"rows": [], "pending": 0})


@pytest.mark.real_model
@pytest.mark.timeout(1500)
def test_a_real_model_run_is_saved_or_says_why_and_a_recording_replays_to_its_digest(tmp_path, real):
    with real.pointer():
        hub = Hub(tmp_path, wait_s=900, hold=already_held)
        job = hub.start_run("fixture_short", "model")
        while job.status == "running":
            threading.Event().wait(0.5)
        assert job.status == "done", job.error
        doc = hub.document(job.id)
        name = sync_recording(tmp_path)
    model = doc["meta"]["backend"]["model"]
    assert model and doc["meta"]["controller"] == "model" and any(r["kind"] == "decision" and r["answers"] for r in doc["rows"])
    saved = (tmp_path / model / "none" / "fixture_short.json").is_file()
    warned = [e for e in job.events if e["type"] == "warning"]
    assert saved != bool(warned), "a finished model run is saved to the run store, or the page is told why it was not"
    replayed = hub.recording(name)
    rec = json.loads((tmp_path / name).read_text())
    assert replayed["meta"]["digest"] == rec["digest"] and replayed["meta"]["backend"]["model"] == model
    rec["decisions"][0]["calls"][0]["state_sha256"] = "0" * 64
    (tmp_path / "changed.json").write_text(json.dumps(rec))
    with pytest.raises(recordings.BadDocument, match="does not replay"):
        hub.recording("changed.json")

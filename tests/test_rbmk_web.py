"""The RBMK control room's server: a real HTTP server on loopback, real plain-code runs, real documents.

No model runs here and none is imitated: the naive controller and the crew script are plain declared policy code, and
the model paths that need the released decider are exercised by the live run, not by a stand-in.
"""

from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from examples.rbmk.web import hub as hub_module, recordings, runner, server
from examples.rbmk.web.hub import Hub
from examples.rbmk.web.jobs import Job

bundle, scenarios, NaiveController = hub_module.bundle, hub_module.scenarios, hub_module.NaiveController


pytest_plugins = ["real_model", "rbmk_run"]


@pytest.fixture
def served(tmp_path: Path):
    hub = Hub(tmp_path)
    srv = server.serve(hub, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield hub, srv.server_address[1]
    srv.shutdown()
    srv.server_close()


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


@pytest.mark.parametrize("path", ["/room/main.js", "/room/room.glb.gz", "/room/instruments.json", "/assets/three.module.js", "/assets/three.core.js",
                                  "/assets/OrbitControls.js", "/assets/RoomEnvironment.js", "/js/app.js", "/css/app.css"])
def test_the_room_and_its_scripts_are_served_from_the_one_server(served, path):
    _, port = served
    assert call(port, "GET", path)[0] == 200


@pytest.mark.parametrize("path", ["/room/shoot.py", "/room/check.mjs", "/room/../web/server.py", "/room/%2e%2e/web/server.py", "/room/vendor",
                                  "/assets/agents.js", "/assets/poolhouse-ui.js", "/assets/../board.js", "/assets/%2e%2e/base.js", "/room/", "/room"])
def test_only_the_rooms_own_files_and_the_three_shared_scripts_are_served(served, path):
    _, port = served
    assert call(port, "GET", path)[0] == 404


@pytest.mark.parametrize("path", ["/../server.py", "/%2e%2e/server.py", "/js/../../hub.py", "/js/nothing.js", "/js", "//etc/passwd",
                                  "/api/nothing"])
def test_only_the_static_files_are_served(served, path):
    _, port = served
    assert call(port, "GET", path)[0] == 404


def test_a_page_from_another_origin_cannot_use_the_server(served):
    _, port = served
    assert call(port, "GET", "/api/info", headers={"Host": "evil.example"})[0] == 403
    assert call(port, "POST", "/api/run", {"scenario": "full_power", "controller": "naive"},
                headers={"Origin": "http://evil.example"})[0] == 403
    assert call(port, "POST", "/api/run", b"{}", headers={"Content-Type": "text/plain"})[0] == 415


def test_bad_requests_are_refused_with_the_reason(served):
    hub, port = served
    assert call(port, "POST", "/api/run", b"not json")[0] == 400
    assert call(port, "POST", "/api/run", b"[]")[0] == 400
    status, body = call(port, "POST", "/api/run", {"scenario": "no_such", "controller": "naive"})
    assert status == 404 and "no_such" in body["error"]
    status, body = call(port, "POST", "/api/run", {"scenario": "full_power", "controller": "crew"})
    assert status == 400 and "1986" in body["error"]
    assert call(port, "POST", "/api/run", b"x" * (server.BODY_LIMIT + 10))[0] == 413
    assert hub.jobs == {}, "a refused request starts nothing"


def test_the_info_names_every_scenario_and_the_model_it_would_use(served):
    _, port = served
    status, info = call(port, "GET", "/api/info")
    assert status == 200
    assert {s["key"] for s in info["scenarios"]} == set(scenarios.SCENARIOS)
    assert info["model"]["pinned"].startswith("strands-decider") and info["model"]["id"] == ""
    assert info["schema"] == bundle.SCHEMA


def test_a_naive_run_streams_rows_decisions_and_a_document(served):
    _, port = served
    job, seen = run_job(port, "stuck_rod")
    kinds = [e["type"] for e in seen]
    assert kinds[0] == "header" and kinds[-1] == "status" and seen[-1]["status"] == "done"
    assert [e["seq"] for e in seen] == list(range(len(seen)))
    assert {"trace", "decision", "event", "caption", "document"} <= set(kinds)
    assert len(seen[0]["rods"]) == 211
    status, doc = call(port, "GET", f"/api/jobs/{job}/document")
    assert status == 200
    assert recordings.check(json.loads(json.dumps(doc)))["meta"]["controller"] == "naive"
    rows = [e["row"] for e in seen if e["type"] == "trace"]
    assert rows == doc["trace"], "the streamed rows are the rows of the document"
    assert any(r["faults"] for r in rows), "the stuck rod is named in the rows after it sticks"
    assert all(set(r["gauges"]) >= {"chA", "chB", "fuel", "press_l"} for r in rows)


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
        assert call(port, "POST", "/api/batch", {"scenarios": ["poisoned_a"], "repeats": 1})[0] == 409
    finally:
        hub.slot.release()


def test_a_batch_needs_scenarios_and_a_sane_repeat_count(served):
    _, port = served
    assert call(port, "POST", "/api/batch", {"scenarios": [], "repeats": 1})[0] == 400
    assert call(port, "POST", "/api/batch", {"scenarios": ["poisoned_a"], "repeats": 0})[0] == 400


def test_only_a_model_run_can_be_asked_again(served):
    hub, port = served
    job, _ = run_job(port, "full_power")
    assert call(port, "GET", f"/api/candidates?job={job}&index=0&cls=insert_rod")[0] == 400
    status, body = call(port, "POST", "/api/whatif", {"source": {"job": job}, "index": 0, "forced": {"cls": "hold", "target": "none"}})
    assert status == 400 and "decision-model" in body["error"]
    assert len(hub.jobs) == 1, "no what-if job was started"


def test_a_resumed_fork_reproduces_the_run_it_was_taken_from(served):
    """The what-if resume continues exactly where the original went when nothing is changed."""
    hub, port = served
    job_id, _ = run_job(port, "load_follow")
    job = hub.job(job_id)
    original = job.runner.res
    index = 3
    fork = original.forks[index]
    branch = Job("t", "whatif", "load_follow", "naive")
    again = runner.resume(fork, job.scn, NaiveController(), branch, None)
    again.run()
    start = fork.tick
    assert again.res.trace == [r for r in original.trace if r["tick"] >= start]
    assert [d["applied"] for d in again.res.decisions] == [d["applied"] for d in original.decisions[index:] if d["kind"] == "decision"]


def test_a_forced_action_changes_the_branch_and_the_choices_are_checked():
    scn = scenarios.scenario("poisoned_a")
    res = runner.WebRunner(scn, NaiveController()).run()
    fork = res.forks[0]
    assert runner.check_forced(fork, {"cls": "hold", "target": "x", "step": "2"}) == {"cls": "hold", "target": "none", "step": "2"}
    for bad in ({"cls": "explode", "target": "none"}, {"cls": "insert_rod", "target": "R-99-99"}, {"cls": "insert_rod", "target": "none", "step": "9"}):
        with pytest.raises(ValueError):
            runner.check_forced(fork, bad)
    branch = Job("t", "whatif", scn.key, "naive")
    plain = runner.resume(fork, scn, NaiveController(), branch, 6)
    plain.run()
    pushed = runner.resume(fork, scn, runner.ForcedController(_Inner(), {"cls": "scram", "target": "none", "step": "1"}), branch, 6)
    pushed.run()
    assert pushed.res.decisions[0]["applied"]["cls"] == "scram" and pushed.res.decisions[0]["source"] == "forced"
    assert pushed.res.trace[-1]["scram"] and not plain.res.trace[-1]["scram"]
    div = runner.divergence(res.trace, pushed.res.trace)
    assert div["first_divergence_tick"] is not None and div["scram"]["branch"] and not div["scram"]["original"]


class _Inner:
    """The inner controller of a forced decision, taken from the plain-code naive rule for a plumbing test."""

    asker = None

    def propose(self, index, plant):
        return NaiveController().propose(index, plant)


def test_divergence_of_identical_rows_is_none():
    rows = [{"tick": t, "P": 0.5, "hazard": 0.1, "scram": False, "damaged": False} for t in range(5)]
    div = runner.divergence(rows, rows)
    assert div["first_divergence_tick"] is None and div["max_abs_dP"] == 0 and div["ticks_compared"] == 5


class SlowNaive(NaiveController):
    """The naive rule with a stated latency, so answers land inside ticks."""

    def propose(self, index, plant):
        prop = super().propose(index, plant)
        prop.latency_ms = 400_000.0
        return prop


def test_every_trace_row_carries_the_gauge_readings_whatever_path_advanced_it():
    scn = scenarios.variant(scenarios.scenario("load_follow"), ticks=14)
    run = runner.WebRunner(scn, SlowNaive())
    run.run()
    assert len(run.res.trace) >= 14 and all("gauges" in row and "faults" in row for row in run.res.trace)


def _good() -> dict[str, Any]:
    rods = [{"id": "R-00-00", "kind": "MR", "x": 0.0, "y": 0.0, "w": 1.0}]
    row = dict.fromkeys(recordings.ROW_KEYS, 0) | {"rods": [0]}
    return {"schema": bundle.SCHEMA, "meta": {"scenario": {"key": "full_power"}, "controller": "model",
                                               "backend": {"model": "m-1", "recorded_at": "2026-01-02"}},
            "trace": [row], "decisions": [], "events": [], "rods": rods, "outcome": {"verdict": "recovered"}}


def test_a_well_formed_document_is_accepted_and_labelled():
    doc = recordings.check(_good())
    assert recordings.label(doc) == "recorded from m-1 on 2026-01-02"
    assert recordings.summary("a.json", doc)["verdict"] == "recovered"


@pytest.mark.parametrize("mutate,why", [
    (lambda d: d.update(schema="other/1"), "schema"),
    (lambda d: d["meta"]["backend"].update(model=""), "model id"),
    (lambda d: d["meta"]["backend"].pop("model"), "model id"),
    (lambda d: d["meta"].update(controller="oracle"), "controller"),
    (lambda d: d.pop("trace"), "trace"),
    (lambda d: d.update(trace=[]), "no rows"),
    (lambda d: d["trace"][0].pop("P"), "lacks a required field"),
    (lambda d: d["trace"][0].update(rods=[0, 1]), "rod positions"),
    (lambda d: d.update(decisions=[{"tick": 0}]), "decision 0"),
    (lambda d: d["meta"].pop("scenario"), "meta.scenario"),
])
def test_a_malformed_document_is_refused_with_the_reason(mutate, why):
    doc = _good()
    mutate(doc)
    with pytest.raises(recordings.BadDocument, match=why):
        recordings.check(doc)


def test_text_that_is_not_json_or_not_an_object_is_refused():
    for text in (b"", b"{", b"[1]", b'"x"', b"\xff\xfe"):
        with pytest.raises(recordings.BadDocument):
            recordings.parse(text)


def test_a_recording_without_a_date_says_so_in_its_label():
    doc = _good()
    del doc["meta"]["backend"]["recorded_at"]
    assert recordings.label(doc) == "recorded from m-1 on a date that was not stored"


@pytest.mark.parametrize("name", ["../x.json", "a/b.json", ".hidden.json", "x.txt", "", "a" * 200 + ".json", "x\n.json", "x.json\n"])
def test_recording_names_are_plain_file_names(tmp_path, name):
    with pytest.raises(recordings.BadDocument, match="not a recording file name"):
        recordings.read(tmp_path, name)


def test_the_recordings_list_shows_files_that_cannot_be_shown(served, tmp_path):
    port = served[1]
    (tmp_path / "good.json").write_text(json.dumps(_good()))
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "old.json").write_text(json.dumps({**_good(), "schema": "rbmk-run/0"}))
    status, rows = call(port, "GET", "/api/recordings")
    by_name = {r["name"]: r for r in rows}
    assert status == 200 and by_name["good.json"]["ok"] and not by_name["broken.json"]["ok"] and "JSON" in by_name["broken.json"]["error"]
    assert not by_name["old.json"]["ok"] and "schema" in by_name["old.json"]["error"]
    assert call(port, "GET", "/api/recordings/broken.json")[0] == 422
    assert call(port, "GET", "/api/recordings/missing.json")[0] == 404
    assert call(port, "POST", "/api/load", b"{")[0] == 422


@pytest.mark.real_model
def test_a_real_model_recording_is_replayed_and_shown_with_its_label(served, short_recording):
    """The recording holds real answers of the decider from a run made now; the page shows them replayed on the simulator."""
    hub, port = served
    folder, FIXTURE = short_recording
    hub.folder = folder
    status, rows = call(port, "GET", "/api/recordings")
    assert status == 200 and [r["name"] for r in rows] == [FIXTURE] and rows[0]["ok"]
    status, shown = call(port, "GET", f"/api/recordings/{FIXTURE}")
    doc = shown["doc"]
    assert status == 200 and shown["label"].startswith("recorded from strands-decider")
    assert doc["schema"] == bundle.SCHEMA and len(doc["trace"]) == 8 and all("gauges" in r for r in doc["trace"])
    recorded = json.loads((folder / FIXTURE).read_text())["decisions"]
    held = [any(a.get("abstained") for c in d["calls"] for n, a in c["answers"].items() if n == "class") for d in recorded]
    assert [d["source"] for d in doc["decisions"] if d["kind"] == "decision"] == ["safety-policy" if h else "model" for h in held]
    first = doc["decisions"][0]["answers"]["class"]
    assert abs(sum(first["scores"].values()) - 1) < 1e-6 and first["model"] == doc["meta"]["backend"]["model"]


@pytest.mark.real_model
def test_a_recording_is_the_source_of_a_what_if(served, short_recording):
    hub, port = served
    folder, FIXTURE = short_recording
    hub.folder = folder
    status, options = call(port, "GET", f"/api/candidates?recording={FIXTURE}&index=1&cls=insert_rod")
    assert status == 200 and "none" in options and any(k.startswith("R-") for k in options)
    assert call(port, "GET", f"/api/candidates?recording={FIXTURE}&index=99&cls=insert_rod")[0] == 404
    assert call(port, "GET", f"/api/candidates?recording={FIXTURE}&index=1&cls=explode")[0] == 400


@pytest.mark.real_model
def test_a_recording_that_does_not_match_the_simulator_is_refused(tmp_path, short_recording):
    folder, FIXTURE = short_recording
    rec = json.loads((folder / FIXTURE).read_text())
    rec["decisions"][0]["calls"][0]["state_sha256"] = "0" * 64
    (tmp_path / "changed.json").write_text(json.dumps(rec))
    rec = json.loads((folder / FIXTURE).read_text())
    rec["digest"] = "f" * 64
    (tmp_path / "digest.json").write_text(json.dumps(rec))
    hub = Hub(tmp_path)
    with pytest.raises(recordings.BadDocument, match="does not replay"):
        hub.recording("changed.json")
    with pytest.raises(recordings.BadDocument, match="does not reproduce"):
        hub.recording("digest.json")

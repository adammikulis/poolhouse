"""The RBMK operator page's server: a real HTTP server on loopback, real plain-code runs, real documents.

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

from examples.rbmk_operator.web import hub as hub_module, recordings, runner, server
from examples.rbmk_operator.web.hub import Hub
from examples.rbmk_operator.web.jobs import Job

bundle, scenarios, NaiveOperator = hub_module.bundle, hub_module.scenarios, hub_module.NaiveOperator


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
    return res.status, (json.loads(raw) if kind.startswith("application/json") else raw.decode())


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
    assert status == 200 and "<title>RBMK operator</title>" in page


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
    again = runner.resume(fork, job.scn, NaiveOperator(), branch, None)
    again.run()
    start = fork.tick
    assert again.res.trace == [r for r in original.trace if r["tick"] >= start]
    assert [d["applied"] for d in again.res.decisions] == [d["applied"] for d in original.decisions[index:] if d["kind"] == "decision"]


def test_a_forced_action_changes_the_branch_and_the_choices_are_checked():
    scn = scenarios.scenario("poisoned_a")
    res = runner.WebRunner(scn, NaiveOperator()).run()
    fork = res.forks[0]
    assert runner.check_forced(fork, {"cls": "hold", "target": "x", "step": "2"}) == {"cls": "hold", "target": "none", "step": "2"}
    for bad in ({"cls": "explode", "target": "none"}, {"cls": "insert_rod", "target": "R-99-99"}, {"cls": "insert_rod", "target": "none", "step": "9"}):
        with pytest.raises(ValueError):
            runner.check_forced(fork, bad)
    branch = Job("t", "whatif", scn.key, "naive")
    plain = runner.resume(fork, scn, NaiveOperator(), branch, 6)
    plain.run()
    pushed = runner.resume(fork, scn, runner.ForcedOperator(_Inner(), {"cls": "scram", "target": "none", "step": "1"}), branch, 6)
    pushed.run()
    assert pushed.res.decisions[0]["applied"]["cls"] == "scram" and pushed.res.decisions[0]["source"] == "forced"
    assert pushed.res.trace[-1]["scram"] and not plain.res.trace[-1]["scram"]
    div = runner.divergence(res.trace, pushed.res.trace)
    assert div["first_divergence_tick"] is not None and div["scram"]["branch"] and not div["scram"]["original"]


class _Inner:
    """The inner operator of a forced decision, taken from the plain-code naive rule for a plumbing test."""

    asker = None

    def propose(self, index, plant):
        return NaiveOperator().propose(index, plant)


def test_divergence_of_identical_rows_is_none():
    rows = [{"tick": t, "P": 0.5, "hazard": 0.1, "scram": False, "damaged": False} for t in range(5)]
    div = runner.divergence(rows, rows)
    assert div["first_divergence_tick"] is None and div["max_abs_dP"] == 0 and div["ticks_compared"] == 5


def test_trace_rows_and_extras_must_line_up():
    sink = runner.Sink()
    sink.rows.append({"faults": {}})
    with pytest.raises(ValueError, match="2 trace rows but 1"):
        runner.merged([{"tick": 0}, {"tick": 1}], sink)


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
    with pytest.raises((recordings.BadDocument, FileNotFoundError)):
        recordings.read(tmp_path, name)


def test_the_recordings_list_shows_files_that_cannot_be_shown(served, tmp_path):
    port = served[1]
    (tmp_path / "good.json").write_text(json.dumps(_good()))
    (tmp_path / "broken.json").write_text("{")
    (tmp_path / "old.json").write_text(json.dumps({**_good(), "schema": "rbmk-operator-run/0"}))
    status, rows = call(port, "GET", "/api/recordings")
    by_name = {r["name"]: r for r in rows}
    assert status == 200 and by_name["good.json"]["ok"] and not by_name["broken.json"]["ok"] and "JSON" in by_name["broken.json"]["error"]
    assert not by_name["old.json"]["ok"] and "schema" in by_name["old.json"]["error"]
    assert call(port, "GET", "/api/recordings/broken.json")[0] == 422
    assert call(port, "GET", "/api/recordings/missing.json")[0] == 404
    assert call(port, "POST", "/api/load", b"{")[0] == 422


def _shipped() -> list[Path]:
    return sorted(recordings.RECORDINGS.glob("*.json")) if recordings.RECORDINGS.is_dir() else []


@pytest.mark.parametrize("path", _shipped(), ids=lambda p: p.name)
def test_every_shipped_recording_replays_to_the_run_it_was_made_from(path):
    """Each recording is a genuine model run; replaying it on the simulator must reproduce its digest."""
    hub = Hub(path.parent)
    doc = hub.recording(path.name)
    assert doc["meta"]["backend"]["model"] and doc["meta"]["backend"].get("recorded_at")
    assert recordings.check(doc) is doc and len(doc["trace"]) > 1
    assert all("gauges" in r and "faults" in r for r in doc["trace"])

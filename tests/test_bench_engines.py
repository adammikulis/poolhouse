"""The engine benchmark's own logic: arms, planted facts, contention counters, figures and the drafting choice."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from poolhouse.bench import engine_workload as workload, engines, gpuwatch, plan_files

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = json.loads(engines.CAMPAIGN.read_text(encoding="utf-8"))


def test_every_arm_names_files_the_campaign_holds_and_drafts_except_the_diagnosis():
    for device in CAMPAIGN["devices"]:
        for arm in engines.arms_of(CAMPAIGN, device):
            named = [arm.model, arm.head, *(load.get("head", "") for load in (arm.tune or {}).get("loads", []))]
            assert all(key in CAMPAIGN["files"] for key in named if key), arm.name
            assert arm.head or arm.name == "diag-nodraft"
            assert arm.spec_type != "draft-dflash" or arm.head == "dflash2"
            assert arm.engine in engines.ENGINES


def test_arms_are_narrowed_in_the_order_asked_and_an_unknown_one_is_refused():
    assert [a.name for a in engines.arms_of(CAMPAIGN, "mac", "main-iq2,fork-iq2")] == ["main-iq2", "fork-iq2"]
    with pytest.raises(ValueError, match="no arm absent"):
        engines.arms_of(CAMPAIGN, "mac", "absent")


def test_each_segment_holds_its_own_fact_and_the_text_is_cut_without_overlap():
    plan = workload.Plan(levels=(100, 300), chars_per_token=4.0)
    text = "".join(f"line {n}\n" for n in range(200))
    first, second = workload.segments(text, plan, "r0")
    assert workload.needle(0, "r0")[1] in first and workload.needle(1, "r0")[1] in second
    assert workload.needle(0, "r0")[1] not in second
    assert workload.needle(0, "r0")[1] != workload.needle(0, "r1")[1]
    with pytest.raises(ValueError, match="ends before level 300"):
        workload.segments(text[:700], plan)


def test_the_corpus_holds_the_text_asked_for_from_the_checkout():
    assert len(workload.corpus(ROOT, 20000)) == 20000


def test_a_tool_call_matches_only_with_the_name_and_every_argument():
    expected = {"name": "rollback", "args": {"service": "billing", "releases": 3}}

    def call(name, arguments):
        return [{"function": {"name": name, "arguments": json.dumps(arguments)}}]

    assert workload.call_matches(call("rollback", {"service": "Billing", "releases": 3}), expected)
    assert not workload.call_matches(call("rollback", {"service": "billing", "releases": "3"}), expected)
    assert not workload.call_matches(call("rollback", {"service": "billing"}), expected)
    assert not workload.call_matches(call("deploy", {"service": "billing", "releases": 3}), expected)
    assert not workload.call_matches(None, expected)


def test_gpu_time_is_summed_per_process_from_apples_listing():
    text = ('+-o AGXDeviceUserClient\n {\n "AppUsage" = ({"accumulatedGPUTime"=5},{"accumulatedGPUTime"=7})\n'
            ' "IOUserClientCreator" = "pid 41, llama-server"\n }\n'
            '+-o AGXDeviceUserClient\n {\n "AppUsage" = ()\n "IOUserClientCreator" = "pid 9, Other"\n }\n')
    assert gpuwatch.apple_times(text) == {(41, "llama-server"): 12, (9, "Other"): 0}


def test_another_processes_gpu_time_marks_the_span_and_its_own_does_not(monkeypatch):
    monkeypatch.setattr(gpuwatch, "cpu_seconds", lambda own: (0.0, 0.0))
    reads = iter([{(1, "server"): 0, (2, "browser"): 0}, {(1, "server"): 10**12, (2, "browser"): 0}])
    alone = gpuwatch.Span([1], lambda: next(reads), "apple").close()
    assert alone["contended"] is False
    reads = iter([{(1, "server"): 0, (2, "browser"): 0}, {(1, "server"): 0, (2, "browser"): 10**12}])
    shared = gpuwatch.Span([1], lambda: next(reads), "apple").close()
    assert shared["contended"] is True and shared["foreign"] == ["browser"]


def test_a_machine_with_no_gpu_counter_claims_nothing_about_the_gpu(monkeypatch):
    monkeypatch.setattr(gpuwatch, "cpu_seconds", lambda own: (0.0, 0.0))
    assert gpuwatch.Span([1], None, "none").close() == {"gpu_watched": False, "contended": None, "foreign_cpu": 0.0}


def test_a_busy_processor_contends_a_span_whatever_the_gpu_says(monkeypatch):
    reads = iter([(0.0, 0.0), (1e9, 0.0)])
    monkeypatch.setattr(gpuwatch, "cpu_seconds", lambda own: next(reads))
    assert gpuwatch.Span([1], None, "none").close()["contended"] is True


def test_processor_time_of_other_processes_is_their_share_of_every_core():
    assert gpuwatch.foreign_cpu((100.0, 10.0), (180.0, 50.0), 10.0, 16) == 0.25
    assert gpuwatch.foreign_cpu((100.0, 10.0), (101.0, 20.0), 10.0, 16) == 0.0


def test_nvidia_processes_without_a_memory_figure_hold_none():
    assert gpuwatch.nvidia_apps("12, 900\n13, [N/A]\n") == {12: 900, 13: 0}


def _read(level, n, ms, **more):
    return {"arm": "a", "session": "s", "kind": "read", "level": level, "prompt_n": n, "prompt_ms": ms, **more}


def test_cold_reading_is_the_sum_up_to_a_level_and_stops_at_a_contended_read():
    got = engines.figures([_read(100, 1000, 1000.0), _read(200, 3000, 1000.0),
                           _read(300, 4000, 1000.0, contended=True), _read(400, 1000, 1000.0)])
    assert got[("a", "read", 200)]["cold_s"] == [2.0]
    assert got[("a", "read", 200)]["cold_tps"] == [2000.0]
    assert "read_tps" not in got[("a", "read", 300)]
    assert "cold_s" not in got[("a", "read", 400)]


def test_the_drafting_chosen_is_the_fastest_over_the_same_requests_and_ignores_contended_ones():
    def row(depth, prompt, ms, **more):
        return {"arm": "a", "setting": {"draft_n_max": depth}, "prompt": prompt, "predicted_n": 100,
                "predicted_ms": ms, "draft_n": 100, "draft_n_accepted": 50, **more}

    got = engines.best_draft([row(2, 0, 2000.0), row(2, 1, 2000.0), row(4, 0, 1000.0), row(4, 1, 1000.0),
                              row(6, 0, 100.0), row(6, 1, 100.0, contended=True)])
    assert got["a"]["draft_n_max"] == 4 and got["a"]["decode_tps"] == 100.0 and got["a"]["acceptance"] == 0.5


def test_an_undrafted_depth_is_compared_with_but_never_chosen():
    def row(depth, ms, **more):
        return {"arm": "a", "setting": {"head": "h", "draft_n_max": depth}, "prompt": 0, "predicted_n": 100,
                "predicted_ms": ms, **more}

    rows = [row(0, 500.0), row(4, 1000.0, draft_n=10, draft_n_accepted=5)]
    assert engines.best_draft(rows)["a"]["draft_n_max"] == 4
    said = engines.tuned(rows)
    assert "nothing drafted" in said[0] and "0.50x undrafted" in said[1]


def test_an_arm_with_other_drafting_than_the_campaigns_is_reported_under_its_own_name():
    arm = engines.arms_of(CAMPAIGN, "mac", "main-iq2")[0]
    assert engines.drafted_arm(arm, {}) == arm
    assert engines.drafted_arm(arm, {"main-iq2": {"draft_n_max": arm.draft_n_max}}) == arm
    other = engines.drafted_arm(arm, {"main-iq2": {"draft_n_max": 2}})
    assert (other.name, other.draft_n_max) == ("main-iq2-chosen", 2)


def test_a_clock_time_already_past_means_tomorrow():
    now = datetime(2030, 1, 1, 23, 0)
    assert engines.moment("", now) is None
    assert engines.moment("06:30", now) == datetime(2030, 1, 2, 6, 30).timestamp()
    assert engines.moment("23:30", now) == datetime(2030, 1, 1, 23, 30).timestamp()


def test_a_file_converted_here_is_never_fetched_and_says_how_it_is_made(tmp_path):
    config = {"files": {"d": {"local": str(tmp_path / "d.gguf"), "made_by": "convert it"}}}
    with pytest.raises(ValueError, match="convert it"):
        plan_files.required(config, ["d"])
    (tmp_path / "d.gguf").write_text("x")
    assert plan_files.required(config, ["d"]) == {"d": tmp_path / "d.gguf"}


def test_a_shard_held_under_another_name_costs_no_download(tmp_path):
    repo = tmp_path / "models--someone--model"
    (repo / "blobs").mkdir(parents=True)
    (repo / "blobs" / "abc").write_text("x")
    (repo / "snapshots" / "rev" / "A").mkdir(parents=True)
    (repo / "snapshots" / "rev" / "A" / "here.gguf").write_text("x")
    config = {"files": {"f": {"repo": "someone/model", "shards": [
        {"path": "A/here.gguf", "bytes": 1}, {"path": "B/linked.gguf", "bytes": 5, "sha256": "abc"},
        {"path": "B/absent.gguf", "bytes": 7, "sha256": "def"}]}}}
    assert [s["download"] for s in plan_files.needed(config, "f", tmp_path)] == [0, 0, 7]
    config["files"]["f"]["shards"] = [{"path": "A/here.gguf", "bytes": 1, "sha256": "replaced-upstream"}]
    assert [s["download"] for s in plan_files.needed(config, "f", tmp_path)] == [1]

"""The RBMK operator example: the toy simulator, the interlocks, the loud stop and the recording replay.

No test here starts a model or touches a GPU. The model answers in `tests/fixtures/rbmk_operator/` are
a small recording of a real run of the released decision checkpoint (`run.py record fixture_short`),
used only to test the recording round trip and replay.
"""

from __future__ import annotations

import copy
import json
import math
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

from rbmk_operator import (  # noqa: E402
    bundle,
    controls,
    reactor as rx,
    scenarios,
)
from rbmk_operator.loop import Runner  # noqa: E402
from rbmk_operator.operator_model import (  # noqa: E402
    CrewOperator,
    LiveAsker,
    ModelOperator,
    ModelStopped,
    NaiveOperator,
    call_with_timeout,
)
from rbmk_operator.plant import Plant  # noqa: E402
from rbmk_operator.rods import Rods, worth  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "rbmk_operator" / "fixture_short.recording.json"


def naive(key: str):
    return Runner(scenarios.scenario(key), NaiveOperator()).run()


def test_a_run_replays_identically_from_its_seed():
    first = bundle.dumps(bundle.export(naive("sensor_fault"), scenarios.scenario("sensor_fault"), {}, False))
    second = bundle.dumps(bundle.export(naive("sensor_fault"), scenarios.scenario("sensor_fault"), {}, False))
    assert first == second


def test_a_different_seed_gives_different_readings():
    a = Plant(scenarios.variant(scenarios.FULL_POWER, seed=1))
    b = Plant(scenarios.variant(scenarios.FULL_POWER, seed=2))
    assert a.reading != b.reading or a.gauges["chA"].shown != b.gauges["chA"].shown


def test_the_nominal_core_is_critical_and_holds_power():
    core = rx.at_power(rx.nominal(), 1.0, (1.0, 1.0))
    assert abs(rx.reactivity(core)) < 1e-6
    for _ in range(12):
        rx.advance(core, 300, 10)
    assert pytest.approx(1.0, abs=0.01) == core.P


def _equilibrium_rho(core: rx.Core, power: float) -> float:
    """Reactivity with void and fuel temperature settled at a given power: its slope is the power coefficient."""
    heat = 0.94 * power + 0.06 * power
    core.alpha = rx.void_of(heat, core.f)
    core.Tf = rx.TC + (rx.TF_NOM - rx.TC) * heat / (0.6 + 0.4 * min(core.f, 1.4))
    return rx.reactivity(core)


def test_more_void_adds_reactivity_the_void_coefficient_is_positive():
    core = rx.nominal()
    low = rx.reactivity(core)
    core.alpha += 0.05
    assert rx.reactivity(core) > low


def test_the_power_coefficient_turns_positive_at_low_margin_and_low_flow_but_not_at_full_power():
    full = rx.at_power(rx.nominal(), 1.0, (1.0, 1.0))
    assert _equilibrium_rho(full, 1.05) < _equilibrium_rho(full, 0.95)
    base = rx.nominal()
    base.pump_on = [True] * 8
    base.pump_s = [1.0] * 8
    pit = rx.at_power(base, 0.0625, (rx.xenon_for_orm(base, 0.0625, 1.2, 7.0), 1.2), orm=7.0)
    pit.f = 0.6
    assert _equilibrium_rho(pit, 0.075) > _equilibrium_rho(pit, 0.05)


def test_xenon_peaks_hours_after_a_shutdown_at_about_twice_equilibrium():
    core = rx.at_power(rx.nominal(), 1.0, (1.0, 1.0))
    core.P = 0.0
    peak, at = 0.0, 0.0
    for minute in range(0, 16 * 60):
        rx._poisons(core, 60.0)
        if peak < core.X:
            peak, at = core.X, minute / 60
    assert 7.0 < at < 11.0
    assert 1.8 < peak < 2.6


def test_the_xenon_chain_matches_a_reference_integration():
    scipy = pytest.importorskip("scipy.integrate")
    gain = rx.LAMBDA_X * (1 + rx.XE_BURN)

    def rhs(_t, y):
        iod, xe = y
        return [rx.LAMBDA_I * (0.0 - iod), gain * rx.XE_VIA_IODINE * iod - rx.LAMBDA_X * xe]

    ref = scipy.solve_ivp(rhs, (0, 9 * 3600), [1.0, 1.0], rtol=1e-9, atol=1e-12)
    core = rx.Core(X=1.0, iod=1.0, P=0.0)
    for _ in range(9 * 60):
        rx._poisons(core, 60.0)
    assert pytest.approx(ref.y[1, -1], rel=0.02) == core.X


def test_a_rod_in_the_centre_is_worth_more_than_one_at_the_edge_and_tilt_moves_the_worth():
    rods = Rods()
    ranked = sorted(rods.rods, key=lambda r: math.hypot(r.x, r.y))
    assert ranked[0].w > 3 * ranked[-1].w
    assert sum(r.w for r in rods.rods) / len(rods.rods) == pytest.approx(1.0, abs=1e-3)
    tilted = Rods(tilt=0.4)
    east = max(tilted.rods, key=lambda r: r.x)
    west = min(tilted.rods, key=lambda r: r.x)
    assert east.w > west.w and len(rods.rods) == 211
    kinds = {k: len(rods.group(k)) for k in ("EP", "AR", "LAR", "USP", "MR")}
    assert kinds == {"EP": 24, "AR": 12, "LAR": 12, "USP": 24, "MR": 139}


def test_the_margin_is_the_worth_weighted_insertion_of_every_rod():
    rods = Rods()
    rods.settle_to(26.0)
    assert rods.orm() == pytest.approx(26.0, abs=1e-6)
    one = rods.group("MR")[0]
    before = rods.orm()
    one.r = one.target = 0.9
    rods._orm = None
    assert rods.orm() == pytest.approx(before + one.w * worth(0.9), abs=1e-6)


def test_a_stuck_rod_does_not_move_and_a_slow_drive_moves_one_fifth_as_fast():
    rods = Rods()
    a, b, c = rods.group("MR")[:3]
    a.fault, b.fault = "stuck", "slow"
    rods.command([a, b, c], 1.0, "test")
    rods.step(5.0)
    assert a.r == 0.0
    assert b.r == pytest.approx(c.r / 5, rel=1e-6) and c.r > 0.2


def test_the_full_stroke_takes_about_twenty_seconds():
    rods = Rods()
    rods.scram()
    rods.step(19.9)
    assert not all(r.r >= 1.0 for r in rods.rods)
    rods.step(0.2)
    assert all(r.r >= 1.0 for r in rods.rods)


def test_the_positive_scram_effect_depends_on_the_margin():
    def peak(orm: float) -> float:
        base = rx.nominal()
        c = rx.at_power(base, 0.06, (rx.xenon_for_orm(base, 0.06, 1.2, orm), 1.2), orm=orm)
        rx.start_scram(c)
        top = -99.0
        for _ in range(8):
            rx.advance(c, 1.0, 0.5)
            top = max(top, rx.tip_rho(c))
        return top
    assert peak(7.0) > 1.0
    assert peak(26.0) == 0.0


def test_a_scram_from_the_poisoned_state_overshoots_far_more_than_one_from_a_healthy_margin():
    def excursion(orm: float) -> float:
        base = rx.nominal()
        c = rx.at_power(base, 0.06, (rx.xenon_for_orm(base, 0.06, 1.2, orm), 1.2), orm=orm)
        rx.start_scram(c)
        rx.advance(c, 10.0, 0.5)
        return c.peak_P
    assert excursion(7.0) > 10 * excursion(26.0)


def test_az5_through_the_gate_is_applied_not_held():
    plant = Plant(scenarios.scenario("full_power"))
    got = plant.act("scram", controls.NONE, "1")
    assert got.cls == "scram" and plant.core.scram


def test_the_margin_rule_refuses_a_withdrawal_that_would_go_below_the_minimum():
    plant = Plant(scenarios.scenario("poisoned_a"))
    plant.prot["ORM_RULE"] = True
    target = next(k for k in controls.candidates("withdraw_rod", plant) if k.startswith("R-"))
    got = plant.act("withdraw_rod", target, "3")
    assert got.cls == "hold" and got.fired[0]["id"] == "ORM_MIN"


def test_defeating_a_protection_is_allowed_and_logged_as_a_visible_flag():
    plant = Plant(scenarios.scenario("full_power"))
    got = plant.act("protection", "defeat:TG_TRIP", "1")
    assert got.cls == "protection" and not plant.prot["TG_TRIP"]
    assert [f["id"] for f in got.fired] == ["PROTECTION_DEFEATED"] and got.fired[0]["severity"] == "defeat"
    assert any("TG_TRIP" in a for a in plant.alarms())


def test_disagreeing_power_channels_block_a_rod_move_but_not_a_scram():
    plant = Plant(scenarios.scenario("full_power"))
    plant.reading = (1.0, 0.35)
    held = plant.act("withdraw_rod", next(iter(controls.candidates("withdraw_rod", plant))), "1")
    assert held.cls == "hold" and held.fired[0]["id"] == "SENSOR_DISAGREE"
    assert plant.act("scram", controls.NONE, "1").cls == "scram"


def test_an_automatic_trip_fires_on_high_power_and_not_when_it_is_the_defeated_margin_rule():
    plant = Plant(scenarios.scenario("poisoned_a"))
    assert [t["id"] for t in plant.trip()] == []
    plant.core.P = 1.3
    assert "POWER_TRIP" in [t["id"] for t in controls.trips(plant)]
    assert not plant.prot["ORM_RULE"]


def test_stuck_rod_scenario_reports_the_fault_in_the_alarms_and_the_rod_panel():
    plant = Plant(scenarios.scenario("stuck_rod"))
    plant.tick = 2
    plant.apply_events()
    assert any(a.startswith("ROD DRIVE FAULT") for a in plant.alarms())


def test_the_historic_replay_follows_the_known_sequence():
    scn = scenarios.scenario("historic_1986")
    res = Runner(scn, CrewOperator()).run()
    first, rows = res.trace[0], res.trace
    assert first["P"] * rx.THERMAL_MW == pytest.approx(30, abs=5)
    assert min(r["orm"] for r in rows if not r["scram"]) < 8
    test_row = next(r for r in rows if r["test"] == "running")
    scram_row = next(r for r in rows if r["scram"])
    assert scram_row["tick"] - test_row["tick"] > 30
    assert max(r["f"] for r in rows) > 1.1 and min(r["f"] for r in rows if r["test"] == "running") < 0.7
    before = [r["P"] for r in rows if r["test"] == "running" and not r["scram"]]
    assert before[-1] > before[0]
    assert max(r["P"] for r in rows if r["scram"]) > 5 * before[-1]
    assert bundle.outcome(res)["verdict"] == "failed"


def test_a_missing_checkpoint_stops_the_run_loudly_before_any_question(tmp_path):
    with pytest.raises(ModelStopped, match="not available"):
        LiveAsker(pointer=str(tmp_path / "no-such-decider"))


def test_a_real_model_failure_at_ask_time_stops_the_run_with_no_action_taken(tmp_path):
    asker = LiveAsker(pointer=str(tmp_path / "no-such-decider"), verify=False)
    runner = Runner(scenarios.scenario("full_power"), ModelOperator(asker))
    rods_before = [r.r for r in runner.plant.core.rods.rods]
    with pytest.raises(ModelStopped, match="failed"):
        runner.run()
    assert runner.res.decisions == []
    assert [r.r for r in runner.plant.core.rods.rods] == rods_before
    assert runner.plant.last_action == "none yet"


def load_recording() -> dict:
    return json.loads(FIXTURE.read_text())


def test_the_recording_names_the_real_model_and_has_complete_probabilities():
    rec = load_recording()
    assert rec["schema"] == bundle.RECORDING and rec["meta"]["backend"]["model"] == "strands-decider-2B-hobson-v19"
    assert rec["meta"]["backend"]["recorded_at"]
    for decision in rec["decisions"]:
        for call in decision["calls"]:
            for name, answer in call["answers"].items():
                assert sum(answer["scores"].values()) == pytest.approx(1.0, abs=1e-6), name
                assert list(answer["scores"]) == list(answer["options"]) and answer["choice"] in answer["scores"]


def test_the_recording_replays_to_the_identical_run_and_the_export_round_trips():
    rec = load_recording()
    scn = scenarios.scenario(rec["meta"]["scenario"]["key"])
    doc = bundle.export(bundle.replay(rec, scn), scn, rec["meta"]["backend"])
    assert bundle.digest(doc) == rec["digest"]
    assert json.loads(bundle.dumps(doc)) == doc
    assert bundle.recording_of(doc)["digest"] == rec["digest"]
    for row in doc["decisions"]:
        assert row["answers"] and row["state_text"] and set(row) >= {"requested", "applied", "interlocks", "flags", "outcome"}
    assert doc["whatif"] and set(doc) >= {"schema", "meta", "rods", "trace", "events", "decisions", "captions", "outcome", "score"}


def test_a_recording_that_does_not_match_the_simulator_is_refused():
    rec = load_recording()
    scn = scenarios.scenario(rec["meta"]["scenario"]["key"])
    bad = copy.deepcopy(rec)
    bad["decisions"][0]["calls"][0]["state_sha256"] = "0" * 64
    with pytest.raises(bundle.ReplayMismatch):
        bundle.replay(bad, scn)
    bad = copy.deepcopy(rec)
    bad["decisions"][0]["calls"] = bad["decisions"][0]["calls"][1:]
    with pytest.raises(bundle.ReplayMismatch):
        bundle.replay(bad, scn)


class SlowRule(NaiveOperator):
    """The naive rule with a stated model latency: the clock tests need a latency, not probabilities."""

    def __init__(self, ms: float) -> None:
        self.ms = ms

    def propose(self, index, plant):
        prop = super().propose(index, plant)
        prop.latency_ms = self.ms
        return prop


def acted(res):
    return [d for d in res.decisions if d["kind"] == "decision" and d["requested"]["cls"] != "hold"]


def test_a_slow_model_is_a_late_operator():
    scn = scenarios.scenario("load_follow")
    quick = Runner(scn, SlowRule(0.0)).run()
    slow = Runner(scn, SlowRule(400_000.0)).run()
    first_quick, first_slow = acted(quick)[0], acted(slow)[0]
    assert first_quick["applied_t_s"] == first_quick["t_s"] and first_quick["applied_tick"] == first_quick["tick"]
    assert first_slow["applied_t_s"] >= first_slow["t_s"] + 400 and first_slow["applied_tick"] > first_slow["tick"]
    assert [r["P"] for r in quick.trace] != [r["P"] for r in slow.trace]


def test_equal_applied_times_give_identical_plant_trajectories_whatever_the_speed_factor():
    scn = scenarios.scenario("load_follow")
    a = Runner(scn, SlowRule(2000.0), speed=1.0).run()
    b = Runner(scn, SlowRule(1000.0), speed=2.0).run()
    assert [r["P"] for r in a.trace] == [r["P"] for r in b.trace]
    assert [d["applied_t_s"] for d in a.decisions] == [d["applied_t_s"] for d in b.decisions]


def test_a_decision_time_during_an_inflight_answer_is_a_visible_busy_row_not_a_silent_drop():
    scn = scenarios.scenario("load_follow")
    res = Runner(scn, SlowRule(2_000_000.0)).run()
    busy = [d for d in res.decisions if d["kind"] == "busy"]
    assert busy and all("MODEL BUSY" in d["note"] and d["applied"]["cls"] == "hold" for d in busy)


def test_the_exported_run_does_not_depend_on_how_it_is_played_back():
    scn = scenarios.scenario("steady" if "steady" in scenarios.SCENARIOS else "full_power")
    res = Runner(scn, SlowRule(500.0)).run()
    assert bundle.dumps(bundle.export(res, scn, {}, False)) == bundle.dumps(bundle.export(res, scn, {}, False))


def test_a_hung_model_call_fails_loudly_after_its_timeout_and_does_not_block_the_caller():
    began = time.monotonic()
    with pytest.raises(ModelStopped, match="did not answer within"):
        call_with_timeout(lambda: time.sleep(30), 0.3, "decision")
    assert time.monotonic() - began < 5

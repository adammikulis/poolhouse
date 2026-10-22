"""The RBMK operator example: the toy plant, the one-way chain from plant to model to plant, and the recording replay.

No test here starts a model or touches a GPU. The tests that need the model are in test_rbmk_experiment_real.py and run with --real-model.
"""

from __future__ import annotations

import ast
import asyncio
import itertools
import json
import math
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

from rbmk_experiment import recorder, scenarios  # noqa: E402
from rbmk_experiment.controls import options as desk  # noqa: E402
from rbmk_experiment.controls.apply import act  # noqa: E402
from rbmk_experiment.decider.model import LiveAsker, ModelStopped, call_with_timeout  # noqa: E402
from rbmk_experiment.decider.policy import (  # noqa: E402
    CrewScript,
    ModelDecider,
    NaiveDecider,
    Proposal,
)
from rbmk_experiment.display import text  # noqa: E402
from rbmk_experiment.live import Compute, LiveRunner  # noqa: E402
from rbmk_experiment.plant import reactor as rx  # noqa: E402
from rbmk_experiment.plant.plant import Plant  # noqa: E402
from rbmk_experiment.plant.rods import Rods, worth  # noqa: E402
from rbmk_experiment.prompt import builder  # noqa: E402
from rbmk_experiment.prompt.provenance import PROVENANCE  # noqa: E402
from rbmk_experiment.runner import Runner, RunOptions  # noqa: E402
from rbmk_experiment.sensors.model import SensorModel  # noqa: E402

PACKAGE = ROOT / "examples" / "rbmk_experiment"
FIXTURE = ROOT / "tests" / "fixtures" / "rbmk_experiment" / "fixture_short.recording.json"
RECORDINGS = PACKAGE / "recordings"


def view_of(key: str, memory: str = "none", **plant_changes):
    plant = Plant(scenarios.scenario(key))
    sensors = SensorModel(plant.scn.seed, memory)
    sensors.print_orm(plant)
    for name, value in plant_changes.items():
        setattr(plant.core, name, value)
    return sensors.observe(plant, 1.0), plant, sensors


# ---- the plant ------------------------------------------------------------------------------------------------
def test_a_run_is_deterministic_from_its_seed():
    scn = scenarios.scenario("sensor_fault")
    assert recorder.digest(Runner(scn, NaiveDecider()).run()) == recorder.digest(Runner(scn, NaiveDecider()).run())


def test_the_nominal_core_is_critical_and_holds_power():
    core = rx.at_power(rx.nominal(), 1.0, (1.0, 1.0))
    assert abs(rx.reactivity(core)) < 1e-6
    for _ in range(12):
        rx.advance(core, 300, 10)
    assert pytest.approx(1.0, abs=0.01) == core.P


def _settled(core: rx.Core, power: float) -> float:
    heat = 0.94 * power + 0.06 * power
    core.alpha = rx.void_of(heat, core.f)
    core.Tf = rx.TC + (rx.TF_NOM - rx.TC) * heat / (0.6 + 0.4 * min(core.f, 1.4))
    return rx.reactivity(core)


def test_more_void_adds_reactivity_the_void_coefficient_is_positive():
    core = rx.nominal()
    low = rx.reactivity(core)
    core.alpha += 0.05
    assert rx.reactivity(core) > low


def test_the_power_coefficient_is_negative_at_full_power_and_positive_at_low_margin_and_low_flow():
    full = rx.at_power(rx.nominal(), 1.0, (1.0, 1.0))
    assert _settled(full, 1.05) < _settled(full, 0.95)
    base = rx.nominal()
    base.pump_on, base.pump_s = [True] * 8, [1.0] * 8
    pit = rx.at_power(base, 0.0625, (rx.xenon_for_orm(base, 0.0625, 1.2, 7.0), 1.2), orm=7.0)
    pit.f = 0.6
    assert _settled(pit, 0.075) > _settled(pit, 0.05)


def test_xenon_peaks_hours_after_a_shutdown_at_about_twice_equilibrium():
    core = rx.at_power(rx.nominal(), 1.0, (1.0, 1.0))
    core.P = 0.0
    peak, at = 0.0, 0.0
    for minute in range(16 * 60):
        rx._poisons(core, 60.0)
        if peak < core.X:
            peak, at = core.X, minute / 60
    assert 7.0 < at < 11.0 and 1.8 < peak < 2.6


def test_the_xenon_chain_matches_a_reference_integration():
    scipy = pytest.importorskip("scipy.integrate")
    gain = rx.LAMBDA_X * (1 + rx.XE_BURN)

    def rhs(_t, y):
        return [rx.LAMBDA_I * (0.0 - y[0]), gain * rx.XE_VIA_IODINE * y[0] - rx.LAMBDA_X * y[1]]

    ref = scipy.solve_ivp(rhs, (0, 9 * 3600), [1.0, 1.0], rtol=1e-9, atol=1e-12)
    core = rx.Core(X=1.0, iod=1.0, P=0.0)
    for _ in range(9 * 60):
        rx._poisons(core, 60.0)
    assert pytest.approx(ref.y[1, -1], rel=0.02) == core.X


def test_a_rod_in_the_centre_is_worth_more_than_one_at_the_edge_and_tilt_moves_the_worth():
    rods = Rods()
    ranked = sorted(rods.rods, key=lambda r: math.hypot(r.x, r.y))
    assert ranked[0].w > 3 * ranked[-1].w and len(rods.rods) == 211
    assert sum(r.w for r in rods.rods) / 211 == pytest.approx(1.0, abs=1e-3)
    tilted = Rods(tilt=0.4)
    assert max(tilted.rods, key=lambda r: r.x).w > min(tilted.rods, key=lambda r: r.x).w
    assert {k: len(rods.group(k)) for k in ("EP", "AR", "LAR", "USP", "MR")} == {"EP": 24, "AR": 12, "LAR": 12, "USP": 24, "MR": 139}


def test_the_margin_is_the_worth_weighted_insertion_of_every_rod():
    rods = Rods()
    rods.settle_to(26.0)
    assert rods.orm() == pytest.approx(26.0, abs=1e-6)
    one = rods.group("MR")[0]
    before = rods.orm()
    one.r = one.target = 0.9
    rods._orm = None
    assert rods.orm() == pytest.approx(before + one.w * worth(0.9), abs=1e-6)


def test_a_stuck_rod_does_not_move_a_slow_drive_moves_one_fifth_as_fast_and_the_stroke_is_twenty_seconds():
    rods = Rods()
    a, b, c = rods.group("MR")[:3]
    a.fault, b.fault = "stuck", "slow"
    rods.command([a, b, c], 1.0, "test")
    rods.step(5.0)
    assert a.r == 0.0 and b.r == pytest.approx(c.r / 5, rel=1e-6)
    full = Rods()
    full.scram()
    full.step(19.9)
    assert not all(r.r >= 1.0 for r in full.rods)
    full.step(0.2)
    assert all(r.r >= 1.0 for r in full.rods)


def _entering(orm: float, rods: int) -> float:
    base = rx.nominal()
    c = rx.at_power(base, 0.06, (rx.xenon_for_orm(base, 0.06, 1.2, orm), 1.2), orm=orm)
    withdrawn = list(c.rods.group("USP"))[:rods]
    c.rods.command(withdrawn, 1.0, "test")
    c.rods.step(3.0)
    return rx.tip_rho(c)


def test_the_positive_scram_effect_is_per_rod_and_depends_on_the_margin():
    assert _entering(7.0, 24) > _entering(7.0, 3) > 0
    assert _entering(26.0, 24) == 0.0


def test_fuel_damage_is_the_fuel_temperature_passing_its_limit_and_there_is_no_cap_on_power():
    core = rx.at_power(rx.nominal(), 1.0, (1.0, 1.0))
    rx.substep(core, 0.5)
    assert not core.damaged
    core.Tf = rx.DAMAGE_TF + 50.0
    rx.substep(core, 0.5)
    assert core.damaged
    big = rx.at_power(rx.nominal(), 1.0, (1.0, 1.0))
    big.rho_ext += 3.0
    for _ in range(12):
        rx.substep(big, 0.5)
    assert big.peak_P > 100.0


def test_a_run_ends_at_damage_at_shutdown_or_with_the_margin_restored_and_steady_and_nowhere_else():
    plant = Plant(scenarios.scenario("poisoned_a"))
    assert not plant.done()
    plant.core.damaged = True
    assert plant.done()
    plant = Plant(scenarios.scenario("poisoned_a"))
    plant.scram()
    assert not plant.done()
    for rod in plant.core.rods.rods:
        rod.r = 1.0
    assert plant.done()
    plant = Plant(scenarios.scenario("poisoned_a"))
    plant.core.rods.settle_to(26.0)
    for _ in range(3):
        plant.end_tick()
    assert plant.done()


def test_plant_protections_trip_and_can_be_switched_off_and_the_margin_rule_is_not_one_of_them():
    plant = Plant(scenarios.scenario("poisoned_a"))
    assert not plant.prot["TG_TRIP"] and plant.core.orm() < rx.ORM_MIN
    plant._protect()
    assert not plant.core.scram
    plant.core.P = 1.3
    plant._protect()
    assert plant.core.scram and any(e.get("protection") == "POWER_HIGH" for e in plant.events)
    other = Plant(scenarios.scenario("full_power"))
    other.tg_disconnected = True
    other._protect()
    assert other.core.scram and any(e.get("protection") == "TG_TRIP" for e in other.events)


# ---- the one-way chain ------------------------------------------------------------------------------------------
def _imports(path: Path) -> set[str]:
    package = ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts[:-1])
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")[: len(package.split(".")) - node.level + 1]
                base = ".".join([*parts, *([base] if base else [])])
            found.add(base)
            found.update(f"{base}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
    return {name.removeprefix("rbmk_experiment.") for name in found if name.startswith("rbmk_experiment")}


LAYERS = {
    "plant": {"sensors", "prompt", "controls", "decider", "display", "runner", "recorder", "live", "whatif"},
    "sensors/view.py": {"plant", "sensors.model", "prompt", "controls", "decider", "display", "runner", "recorder", "live", "scenarios"},
    "controls/options.py": {"plant", "sensors.model", "prompt", "decider", "display", "runner", "recorder", "live", "scenarios", "controls.apply"},
    "prompt": {"plant", "sensors.model", "decider", "display", "runner", "recorder", "live", "scenarios", "controls.apply"},
    "decider/policy.py": {"plant", "sensors.model", "display", "runner", "recorder", "live", "scenarios", "controls.apply"},
    "decider/model.py": {"plant", "sensors", "prompt", "controls", "display", "runner", "recorder", "live", "scenarios"},
    "display": {"plant", "sensors", "prompt.builder", "decider", "controls.apply", "live", "whatif"},
}


def _forbidden(path: Path, banned: set[str], imported: set[str]) -> list[str]:
    return sorted(i for i in imported if any(i == b or i.startswith(b + ".") for b in banned)
                  and not (path.name == "export.py" and i in ("controls.options",)))


def test_the_prompt_builder_and_the_layers_around_the_model_cannot_import_the_plant():
    for rule, banned in LAYERS.items():
        target = PACKAGE / rule
        for path in ([target] if target.suffix else sorted(target.rglob("*.py"))):
            bad = _forbidden(path, banned, _imports(path))
            assert not bad, f"{path.relative_to(ROOT)} imports {bad}"


def test_nothing_in_the_example_substitutes_for_a_model_answer():
    banned = re.compile(r"abstain|safety.policy|flag a person|hold and flag", re.IGNORECASE)
    for path in sorted(PACKAGE.rglob("*.py")):
        if "web" in path.parts:
            continue
        assert not banned.search(path.read_text()), path.name


# ---- the prompt -------------------------------------------------------------------------------------------------
STEERING = re.compile(r"dangerous|\bsafe\b|about to|educational|toy|RBMK-type|defeat|right answer|should|must be|hold and", re.IGNORECASE)


def test_every_prompt_item_has_provenance_and_the_console_carries_no_steering_words():
    view, *_ = view_of("poisoned_a", memory="history")
    panel = builder.console(view)
    assert set(panel.items) <= set(PROVENANCE) and {"briefing", "orm_printout", "protection_switches", "desk_log"} <= set(panel.items)
    shown = panel.text.replace("Standing briefing.", "")
    assert not STEERING.search(shown.replace("the written operating rules require", "")), STEERING.search(shown)
    plain, *_ = view_of("poisoned_a")
    assert "strip_recorders" not in builder.console(plain).items and "strip_recorders" in panel.items
    for key in ("neutron", "rods", "coolant", "steam", "switches"):
        builder.check_provenance(builder.PANELS[key](view))


def test_the_prompt_is_a_function_of_the_sensor_view_only():
    a, _plant_a, _ = view_of("poisoned_a")
    _b, plant_b, _ = view_of("poisoned_a")
    plant_b.core.X, plant_b.core.alpha, plant_b.core.Tf, plant_b.core.rho = 4.0, 0.5, 900.0, 0.3
    plant_b.core.rods.group("MR")[0].fault = "stuck"
    again = SensorModel(plant_b.scn.seed)
    again.print_orm(plant_b)
    assert builder.console(again.observe(plant_b, 1.0)).text == builder.console(a).text
    plant_b.core.P = 0.5
    plant_b.core.t += 5.0
    assert builder.console(again.observe(plant_b, 1.0)).text != builder.console(a).text


def test_the_orm_comes_only_from_the_printout_and_ages_until_it_is_asked_for_again():
    view, plant, sensors = view_of("poisoned_a")
    assert view.orm_printout == 7.0
    plant.core.rods.command(plant.core.rods.group("USP"), 1.0, "test")
    plant.substeps(30.0, 10.0)
    stale = sensors.observe(plant, 30.0)
    assert stale.orm_printout == 7.0 and stale.orm_age_s >= 30.0
    act("orm_print", desk.NONE, "1", plant, sensors)
    assert sensors.view(plant).orm_printout > 7.0 and sensors.view(plant).orm_age_s == 0.0


def test_option_lists_carry_ids_and_positions_only_and_are_logged_in_a_seeded_order():
    view, *_ = view_of("poisoned_a")
    options = desk.target_options("withdraw_rod", view, "s:options")
    assert all(not re.search(r"worth|zone|best|effective", t) for t in options.values())
    assert options == desk.target_options("withdraw_rod", view, "s:options") and options != desk.target_options("withdraw_rod", view, "t:options")
    a = builder.decision_call(view, "1:0")[2]["class"]
    b = builder.decision_call(view, "1:1")[2]["class"]
    assert [o.name for o in a.options] != [o.name for o in b.options] and {o.name for o in a.options} == set(desk.CLASSES)
    assert all(list(desk.CLASSES.values()).count(o.description) == 1 for o in a.options)


def test_a_withdrawal_below_the_written_margin_is_allowed_the_plant_does_not_refuse_it():
    view, plant, sensors = view_of("poisoned_a")
    target = next(k for k in desk.target_options("withdraw_rod", view, "x") if k.startswith("R-"))
    line = act("withdraw_rod", target, "3", plant, sensors)
    assert line.startswith("withdrew") and not plant.events


def test_a_request_the_plant_cannot_act_on_does_nothing_and_is_a_plant_event():
    _, plant, sensors = view_of("full_power")
    assert act("turbine_test", "test:abort", "1", plant, sensors) == ""
    assert plant.events[-1]["kind"] == "request_ignored"
    act("scram", desk.NONE, "1", plant, sensors)
    assert act("insert_rod", "grp:MR", "1", plant, sensors) == "" and plant.events[-1]["kind"] == "request_ignored"


# ---- the display --------------------------------------------------------------------------------------------------
VERDICT = re.compile(r"recover|fail|better|worse|right answer|correct|wrong|should|good|bad", re.IGNORECASE)


def test_the_display_states_events_and_measured_end_states_and_grades_nothing():
    for key in ("full_power", "poisoned_a", "historic_1986"):
        scn = scenarios.scenario(key)
        res = Runner(scn, CrewScript() if scn.crew else NaiveDecider()).run()
        out = text.outcome(res)
        assert set(out) >= {"state", "damaged", "final_margin", "peak_MW", "az5_pressed"} and out["state"] in (
            "damaged", "margin_below_15", "margin_at_least_15")
        for caption in text.captions(res):
            assert not VERDICT.search(caption["text"]), caption
        assert not VERDICT.search(text.description(scn)) and not VERDICT.search(text.TITLES[key])


def test_a_recording_holds_no_caption_title_label_or_score():
    scn = scenarios.scenario("full_power")
    res = Runner(scn, NaiveDecider()).run()
    rec = recorder.recording_of(res, scn, {"model": "none"}, {})
    assert not re.search(r"caption|title|blurb|verdict|score|label", json.dumps(rec), re.IGNORECASE)


# ---- the 1986 sequence ---------------------------------------------------------------------------------------------
def test_the_scripted_crew_follows_the_known_sequence():
    scn = scenarios.scenario("historic_1986")
    res = Runner(scn, CrewScript()).run()
    rows = res.trace
    assert rows[0]["P"] * rx.THERMAL_MW == pytest.approx(30, abs=5)
    assert min(r["orm"] for r in rows if not r["scram"]) < 8
    running = [r for r in rows if r["test"] == "running"]
    scram = next(r for r in rows if r["scram"])
    assert 30 < scram["t_s"] - running[0]["t_s"] < 60
    assert max(r["f"] for r in rows) > 1.1 and min(r["f"] for r in running) < 0.7
    assert max(r["peak_P"] for r in rows) > 2.0 and text.outcome(res)["damaged"]


def test_the_decider_keeps_being_asked_after_az5_and_asks_back_to_back():
    class ScramThenHold:
        kind = "naive"

        def __init__(self) -> None:
            self.asked = 0

        def propose(self, n, view):
            self.asked += 1
            return Proposal("scram" if n == 0 else "hold", desk.NONE, "1", latency_ms=5000.0)

    scn = scenarios.variant(scenarios.scenario("full_power"), fast_from=0, ticks=100)
    decider = ScramThenHold()
    res = Runner(scn, decider, RunOptions(speed=1.0)).run()
    rows = res.decisions
    assert len(rows) > 2 and any(r["view"]["az5"] for r in rows[1:])
    for prev, row in itertools.pairwise(rows):
        assert abs(row["t_s"] - prev["due_s"]) < 0.06
    assert all(r["delay_s"] == pytest.approx(5.0, abs=0.01) for r in rows)


# ---- the model: failure is loud --------------------------------------------------------------------------------------
def test_a_missing_checkpoint_stops_the_run_loudly_before_any_question(tmp_path):
    with pytest.raises(ModelStopped, match="not available"):
        LiveAsker(pointer=str(tmp_path / "no-such-decider"))


def test_a_real_model_failure_at_ask_time_stops_the_run_and_nothing_was_applied(tmp_path):
    asker = LiveAsker(pointer=str(tmp_path / "no-such-decider"), verify=False)
    runner = Runner(scenarios.scenario("full_power"), ModelDecider(asker, 0))
    rods_before = [r.r for r in runner.plant.core.rods.rods]
    with pytest.raises(ModelStopped, match="failed"):
        runner.run()
    assert runner.res.decisions == [] and [r.r for r in runner.plant.core.rods.rods] == rods_before
    assert runner.plant.events == []


# ---- the clocks -----------------------------------------------------------------------------------------------------------
class RealSleep(NaiveDecider):
    """The naive rule behind a real, blocking wait: a slow call with a measured latency, not a model."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds

    def propose(self, n, view):
        began = time.perf_counter()
        time.sleep(self.seconds)
        prop = super().propose(n, view)
        prop.latency_ms = (time.perf_counter() - began) * 1000.0
        return prop


def live(seconds: float, *, ticks: int = 14, speed: float = 3000.0, timeout: float = 5.0, compute=None):
    scn = scenarios.variant(scenarios.scenario("load_follow"), ticks=ticks)
    return LiveRunner(Runner(scn, RealSleep(seconds), RunOptions(speed=speed)), speed=speed, timeout=timeout, compute=compute or Compute())


def test_a_slow_model_call_does_not_stop_the_plant():
    res = asyncio.run(live(0.35).run())
    assert res.decisions and all(d["applied_tick"] - d["tick"] >= 2 for d in res.decisions if d["applied_tick"] is not None)
    assert len(res.trace) >= 14


def test_a_slow_model_is_a_late_operator_and_equal_applied_times_give_identical_trajectories():
    scn = scenarios.variant(scenarios.scenario("load_follow"), ticks=14)

    class Latency(NaiveDecider):
        def __init__(self, ms):
            self.ms = ms

        def propose(self, n, view):
            prop = super().propose(n, view)
            prop.latency_ms = self.ms
            return prop

    quick = Runner(scn, Latency(1000.0), RunOptions(speed=1.0)).run()
    slow = Runner(scn, Latency(400_000.0), RunOptions(speed=1.0)).run()
    assert slow.decisions[0]["applied_t_s"] >= 400 > quick.decisions[0]["applied_t_s"]
    for row in (r for r in quick.decisions if r["applied_t_s"] is not None):
        assert row["applied_t_s"] - row["t_s"] >= row["latency_ms"] / 1000.0 - 0.1 > 0.0, "an answer lands after its latency, not at once"
    assert [r["P"] for r in quick.trace] != [r["P"] for r in slow.trace]
    a = Runner(scn, Latency(2000.0), RunOptions(speed=1.0)).run()
    b = Runner(scn, Latency(1000.0), RunOptions(speed=2.0)).run()
    assert [r["P"] for r in a.trace] == [r["P"] for r in b.trace]


def test_the_live_trajectory_equals_the_synchronous_one_at_the_same_applied_times():
    scn = scenarios.variant(scenarios.scenario("load_follow"), ticks=14)
    res = asyncio.run(live(0.35).run())
    rows = {d["n"]: d for d in res.decisions}

    class Fixed(NaiveDecider):
        def propose(self, n, view):
            prop = super().propose(n, view)
            prop.latency_ms = rows[n]["latency_ms"] if n in rows else 1e9
            return prop

    again = Runner(scn, Fixed(), RunOptions(speed=3000.0, landing=lambda n: rows[n]["due_s"] if n in rows else None)).run()
    assert [r["P"] for r in again.trace] == [r["P"] for r in res.trace]


def test_a_hung_call_halts_the_run_loudly_and_its_late_answer_is_discarded_and_logged():
    compute = Compute()

    async def scenario_run():
        run = live(1.5, timeout=0.3, compute=compute)
        with pytest.raises(ModelStopped, match="did not answer within"):
            await run.run()
        assert compute.dead == 1
        with pytest.raises(ModelStopped, match="still held"):
            await live(0.01, compute=compute).run()
        await asyncio.sleep(2.0)
        return run

    run = asyncio.run(scenario_run())
    late = run.result.late
    assert compute.dead == 0 and late and "late answer discarded after halt" in late[0]["note"] and late[0]["latency_ms"] >= 1000
    assert not run.result.decisions


def test_a_hung_call_in_the_timeout_wrapper_fails_after_its_timeout_and_does_not_block_the_caller():
    began = time.monotonic()
    with pytest.raises(ModelStopped, match="did not answer within"):
        call_with_timeout(lambda: time.sleep(30), 0.3, "decision")
    assert time.monotonic() - began < 5


def test_stopping_a_live_run_ends_it_without_applying_an_in_flight_answer():
    async def scenario_run():
        run = live(0.8, ticks=60, speed=300.0)
        task = asyncio.create_task(run.run())
        await asyncio.sleep(0.3)
        run.stop()
        await task
        await asyncio.sleep(1.2)
        return run

    run = asyncio.run(scenario_run())
    assert run.result.late and "late answer discarded after halt" in run.result.late[0]["note"]
    assert not run.result.decisions

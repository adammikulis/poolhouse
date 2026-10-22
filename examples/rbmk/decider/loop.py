"""The decision loop with three separate clocks.

The plant steps with fixed substeps (10 s slow, 0.5 s fast) and never waits for the model. The decider is asked on its
own cadence (every 3 slow ticks, or every 5 s in the fast regime). A model answer lands in the plant at the simulated
time that matches its real latency (``speed`` simulated seconds per wall second), so a slow model is a late decider;
a decision time that arrives while an answer is in flight is logged as a MODEL BUSY row, never dropped or queued
silently. Playback speed belongs to an interface and changes none of this: a run is keyed to its applied ticks.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..sim import reactor as rx
from ..sim.plant import Plant
from ..sim.scenarios import Scenario
from .controllers import CrewController, ModelController, NaiveController

OUTCOME_AFTER = 4
Controller = ModelController | NaiveController | CrewController


@dataclass
class Result:
    """One finished (or in-progress) run: its rows, and the plant forks taken at each decision."""

    scenario: str
    controller: str
    trace: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    forks: dict[int, Plant] = field(default_factory=dict)
    finished: bool = False
    speed: float = 1.0


def _row_of(got: Any) -> dict[str, str]:
    return {"cls": got.cls, "target": got.target, "step": got.step}


def _batch(prop: Any) -> dict[str, Any]:
    calls = [{"key": c["key"], "size": len(c["answers"]), "latency_ms": c["latency_ms"],
              "sent_at": c["sent_at"], "answered_at": c["answered_at"],
              "tokens_max": max((a.get("tokens", 0) for a in c["answers"].values()), default=0)} for c in prop.calls]
    return {"calls": calls, "questions": sum(c["size"] for c in calls),
            "latency_ms": round(sum(c["latency_ms"] for c in calls), 3)}


def _decision_row(index: int, plant: Plant, text: str, prop: Any, delay_s: float) -> dict[str, Any]:
    risk = prop.answers.get("risk", {}).get("expected") if prop.answers else None
    return {"i": index, "kind": "decision", "tick": plant.tick, "t_s": round(plant.core.t, 1),
            "snapshot_tick": plant.tick, "fast": plant.fast(), "state_text": text, "state": plant.sample(),
            "answers": prop.answers, "calls": prop.calls, "batch": _batch(prop), "model_choice": prop.model_choice,
            "requested": {"cls": prop.cls, "target": prop.target, "step": prop.step}, "applied": None,
            "applied_tick": None, "applied_t_s": None, "delay_s": round(delay_s, 3), "source": prop.source,
            "abstained": prop.source == "safety-policy", "interlocks": [], "flags": [], "note": prop.note,
            "latency_ms": round(prop.latency_ms, 1), "model_risk": risk}


def _outcomes(res: Result) -> None:
    by_tick = {row["tick"]: row for row in res.trace}
    last = res.trace[-1]
    for d in res.decisions:
        after = by_tick.get(d["tick"] + OUTCOME_AFTER, last)
        before = d["state"]
        delta = after["hazard"] - before["hazard"]
        verdict = "worse" if delta > 0.1 else "better" if delta < -0.1 else "steady"
        d["outcome"] = {"tick": after["tick"], "P": after["P"], "hazard": after["hazard"],
                        "hazard_before": before["hazard"], "P_before": before["P"], "verdict": verdict}


class Runner:
    """Runs a scenario with one controller, calling ``progress`` after each decision."""

    def __init__(self, scn: Scenario, controller: Controller, progress: Callable[[Result], None] | None = None,
                 speed: float = 1.0) -> None:
        self.scn, self.controller, self.progress, self.speed = scn, controller, progress, speed
        self.res = Result(scn.key, controller.kind, speed=speed)
        self.plant = Plant(scn)
        self.risk: float | None = None
        self.model_choice: dict[str, str] | None = None
        self.inflight: dict[str, Any] | None = None

    def _land(self) -> None:
        """Apply the in-flight action at this simulated moment."""
        plant, fl = self.plant, self.inflight
        if fl is None:
            raise RuntimeError("no action is in flight")
        prop, row = fl["prop"], fl["row"]
        got = plant.act(prop.cls, prop.target, prop.step)
        fired = [i for i in got.fired if i["severity"] == "block"]
        row.update(applied=_row_of(got), applied_tick=plant.tick, applied_t_s=round(plant.core.t, 1),
                   interlocks=fired, flags=[i for i in got.fired if i["severity"] != "block"])
        if fired:
            row["source"] = "interlock"
        self.inflight = None
        if self.progress:
            self.progress(self.res)

    def _decide(self) -> None:
        plant = self.plant
        index = len(self.res.decisions)
        text = plant.state_text()
        fork = copy.deepcopy(plant)
        plant.since_decision = 0
        prop = self.controller.propose(index, plant)
        delay = prop.latency_ms / 1000.0 * self.speed
        self.risk = prop.answers["risk"]["expected"] if prop.answers else self.risk
        self.model_choice = prop.model_choice
        self.res.forks[index] = fork
        row = _decision_row(index, plant, text, prop, delay)
        self.res.decisions.append(row)
        self.inflight = {"prop": prop, "row": row, "due_s": plant.core.t + delay}
        if delay <= 1e-9:
            self._land()

    def _busy(self) -> None:
        plant, fl = self.plant, self.inflight
        if fl is None:
            raise RuntimeError("no action is in flight")
        plant.since_decision = 0
        self.res.decisions.append({
            "i": len(self.res.decisions), "kind": "busy", "tick": plant.tick, "t_s": round(plant.core.t, 1),
            "fast": plant.fast(), "state_text": "", "state": plant.sample(), "answers": {}, "calls": [],
            "model_choice": None, "requested": None, "applied": {"cls": "hold", "target": "none", "step": "1"},
            "applied_tick": plant.tick, "applied_t_s": round(plant.core.t, 1), "source": "busy", "abstained": False,
            "interlocks": [], "flags": [], "latency_ms": 0.0, "model_risk": self.risk,
            "note": (f"MODEL BUSY: the answer to the request at tick {fl['row']['snapshot_tick']} lands at "
                     f"t={fl['due_s']:.1f} s; this decision time was skipped, nothing was queued")})

    def _tick(self) -> None:
        plant = self.plant
        for e in plant.apply_events():
            self.res.events.append({"tick": plant.tick, "kind": e.kind, "text": e.caption})
        plant.sense()
        if plant.due():
            if self.inflight:
                self._busy()
            else:
                self._decide()
        row = plant.sample()
        row["model_risk"], row["tip"] = self.risk, round(rx.tip_rho(plant.core), 4)
        self.res.trace.append(row)
        first = None if self.inflight is None else self.inflight["due_s"] - plant.core.t
        if first is not None and first < plant.regime()[0]:
            plant.advance_split(max(first, 0.0), self._land)
        else:
            plant.advance()
        fired = plant.trip()
        if fired:
            self.res.decisions.append({
                "i": len(self.res.decisions), "kind": "trip", "tick": plant.tick, "t_s": round(plant.core.t, 1),
                "fast": plant.fast(), "state_text": "", "state": plant.sample(), "answers": {}, "calls": [],
                "model_choice": self.model_choice, "requested": None,
                "applied": {"cls": "scram", "target": "none", "step": "1"}, "applied_tick": plant.tick,
                "applied_t_s": round(plant.core.t, 1), "flags": [], "source": "interlock", "abstained": False,
                "interlocks": fired, "note": "automatic trip, no question asked", "latency_ms": 0.0,
                "model_risk": self.risk})

    def run(self) -> Result:
        """Run to the end of the scenario and return the rows."""
        while not self.plant.done():
            self._tick()
        if self.inflight:
            self.inflight["row"]["note"] += " The run ended before this action landed."
        self.plant.sense()
        last = self.plant.sample()
        last["model_risk"], last["tip"] = self.risk, 0.0
        self.res.trace.append(last)
        _outcomes(self.res)
        self.res.finished = True
        return self.res

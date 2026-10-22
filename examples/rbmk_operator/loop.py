"""The decision loop with three separate clocks.

The plant steps with fixed substeps (10 s slow, 0.5 s fast) and never waits for the model. The operator is asked on its
own cadence (every 3 slow ticks, or every 5 s in the fast regime). A model answer lands in the plant at the simulated
time that matches its real latency (``speed`` simulated seconds per wall second), so a slow model is a late operator;
a decision time that arrives while an answer is in flight is logged as a MODEL BUSY row, never dropped or queued
silently. Playback speed belongs to an interface and changes none of this: a run is keyed to its applied ticks.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import reactor as rx
from .operator_model import CrewOperator, ModelOperator, NaiveOperator
from .plant import Plant
from .scenarios import Scenario

OUTCOME_AFTER = 4
Operator = ModelOperator | NaiveOperator | CrewOperator


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
    late: list[dict[str, Any]] = field(default_factory=list)


def _row_of(got: Any) -> dict[str, str]:
    return {"cls": got.cls, "target": got.target, "step": got.step}


def _batch(prop: Any) -> dict[str, Any]:
    calls = [{"key": c["key"], "size": len(c["answers"]), "latency_ms": c["latency_ms"],
              "sent_at": c["sent_at"], "answered_at": c["answered_at"],
              "tokens_max": max((a.get("tokens", 0) for a in c["answers"].values()), default=0)} for c in prop.calls]
    rounds: dict[tuple[float, float], list[str]] = {}
    for c in calls:
        rounds.setdefault((c["sent_at"], c["answered_at"]), []).append(c["key"])
    return {"calls": calls, "questions": sum(c["size"] for c in calls), "latency_ms": round(sum(c["latency_ms"] for c in calls), 3),
            "rounds": [{"keys": keys, "sent_at": s, "answered_at": a, "wall_ms": round((a - s) * 1000.0, 1)}
                       for (s, a), keys in rounds.items()]}


def _decision_row(index: int, n: int, snap: dict[str, Any], prop: Any, delay_s: float) -> dict[str, Any]:
    risk = prop.answers.get("risk", {}).get("expected") if prop.answers else None
    return {"i": index, "n": n, "kind": "decision", "tick": snap["tick"], "t_s": snap["t_s"],
            "snapshot_tick": snap["tick"], "fast": snap["fast"], "state_text": snap["text"], "state": snap["state"],
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
    """Runs a scenario with one operator. The steps (`begin_tick`, `request`, `receive`, `finish_tick`) are plain
    synchronous functions in simulated time; `run` chains them, `live.LiveRunner` schedules them on a clock."""

    def __init__(self, scn: Scenario, operator: Operator, progress: Callable[[Result], None] | None = None,
                 speed: float = 1.0, landing: Callable[[int], float | None] | None = None) -> None:
        self.scn, self.operator, self.progress, self.speed = scn, operator, progress, speed
        self.landing = landing or getattr(operator, "landing", lambda n: None)
        self.res = Result(scn.key, operator.kind, speed=speed)
        self.plant = Plant(scn)
        self.risk: float | None = None
        self.model_choice: dict[str, str] | None = None
        self.inflight: dict[str, Any] | None = None
        self.waiting = False
        self.requests = 0

    def _land(self) -> None:
        """Apply the in-flight action at this simulated moment."""
        plant, fl = self.plant, self.inflight
        if fl is None:
            raise RuntimeError("no action is in flight")
        prop, row = fl["prop"], fl["row"]
        if getattr(prop, "printed", False):
            plant.print_orm()
        got = plant.act(prop.cls, prop.target, prop.step)
        fired = [i for i in got.fired if i["severity"] == "block"]
        row.update(applied=_row_of(got), applied_tick=plant.tick, applied_t_s=round(plant.core.t, 1),
                   interlocks=fired, flags=[i for i in got.fired if i["severity"] != "block"])
        if fired:
            row["source"] = "interlock"
        self.inflight = None
        if self.progress:
            self.progress(self.res)

    def begin_tick(self) -> bool:
        """Events and sensors for this tick; true when the operator is due."""
        plant = self.plant
        for e in plant.apply_events():
            self.res.events.append({"tick": plant.tick, "kind": e.kind, "text": e.caption})
        plant.sense()
        return plant.due()

    def request(self) -> tuple[int, Plant, dict[str, Any], Plant]:
        """Take the state snapshot for a model request: its number, the copy the model reads, the snapshot row and the fork."""
        plant = self.plant
        n, self.requests = self.requests, self.requests + 1
        plant.since_decision = 0
        snapshot = {"text": plant.state_text(), "state": plant.sample(), "tick": plant.tick,
                    "t_s": round(plant.core.t, 1), "t": plant.core.t, "fast": plant.fast()}
        fork = copy.deepcopy(plant)
        self.waiting = True
        return n, copy.deepcopy(plant), snapshot, fork

    def receive(self, n: int, snapshot: dict[str, Any], fork: Plant, prop: Any) -> None:
        """An answer has arrived: log the row and land it at snapshot time plus latency times speed."""
        plant = self.plant
        self.waiting = False
        delay = prop.latency_ms / 1000.0 * self.speed
        index = len(self.res.decisions)
        self.risk = prop.answers["risk"]["expected"] if prop.answers else self.risk
        self.model_choice = prop.model_choice
        self.res.forks[index] = fork
        row = _decision_row(index, n, snapshot, prop, delay)
        self.res.decisions.append(row)
        due = self.landing(n)
        due = snapshot["t"] + delay if due is None else due
        self.inflight = {"prop": prop, "row": row, "due_s": max(due, plant.core.t)}
        row["due_s"] = self.inflight["due_s"]
        if self.inflight["due_s"] <= plant.core.t + 1e-9:
            self._land()

    def busy(self) -> None:
        """A decision time arrived while an answer is in flight: a visible row, nothing queued."""
        plant = self.plant
        plant.since_decision = 0
        waiting = self.inflight["row"]["snapshot_tick"] if self.inflight else "an earlier tick"
        due = f"lands at t={self.inflight['due_s']:.1f} s" if self.inflight else "has not arrived yet"
        self.res.decisions.append({
            "i": len(self.res.decisions), "kind": "busy", "tick": plant.tick, "t_s": round(plant.core.t, 1),
            "fast": plant.fast(), "state_text": "", "state": plant.sample(), "answers": {}, "calls": [],
            "model_choice": None, "requested": None, "applied": {"cls": "hold", "target": "none", "step": "1"},
            "applied_tick": plant.tick, "applied_t_s": round(plant.core.t, 1), "source": "busy", "abstained": False,
            "interlocks": [], "flags": [], "latency_ms": 0.0, "model_risk": self.risk,
            "note": f"MODEL BUSY: the answer to the request at tick {waiting} {due}; this decision time was skipped, nothing was queued"})

    def next_wall_s(self, speed: float) -> float:
        """Wall seconds the coming tick takes at ``speed`` simulated seconds per wall second."""
        return self.plant.regime()[0] / speed

    def finish_tick(self) -> None:
        """Sample, advance the plant one tick (landing an arrived answer inside it), and run the trips."""
        plant = self.plant
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

    def finish_run(self) -> Result:
        """Close the run: final sample, outcomes."""
        if self.inflight:
            self.inflight["row"]["note"] += " The run ended before this action landed."
        self.plant.sense()
        last = self.plant.sample()
        last["model_risk"], last["tip"] = self.risk, 0.0
        self.res.trace.append(last)
        _outcomes(self.res)
        self.res.finished = True
        return self.res

    def run(self) -> Result:
        """Run to the end of the scenario and return the rows."""
        while not self.plant.done():
            if self.begin_tick():
                if self.inflight:
                    self.busy()
                else:
                    n, snap_plant, snapshot, fork = self.request()
                    self.receive(n, snapshot, fork, self.operator.propose(n, snap_plant))
            self.finish_tick()
        return self.finish_run()

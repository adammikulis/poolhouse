"""The decision loop: sense, ask the operator, pass the answer through the interlocks, advance."""

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

    def public(self) -> dict[str, Any]:
        """JSON-ready rows (the forks are not part of it)."""
        return {"scenario": self.scenario, "controller": self.controller, "trace": self.trace,
                "decisions": self.decisions, "events": self.events, "finished": self.finished}


def _row_of(got: Any) -> dict[str, str]:
    return {"cls": got.cls, "target": got.target, "step": got.step}


def _decision_row(index: int, plant: Plant, text: str, prop: Any, got: Any) -> dict[str, Any]:
    fired = [i for i in got.fired if i["severity"] == "block"]
    flags = [i for i in got.fired if i["severity"] != "block"]
    source = "interlock" if fired else prop.source
    risk = prop.answers.get("risk", {}).get("expected") if prop.answers else None
    return {"i": index, "kind": "decision", "tick": plant.tick, "t_s": round(plant.core.t, 1),
            "fast": plant.fast(), "state_text": text, "state": plant.sample(), "answers": prop.answers,
            "calls": prop.calls, "model_choice": prop.model_choice,
            "requested": {"cls": prop.cls, "target": prop.target, "step": prop.step}, "applied": _row_of(got),
            "source": source, "abstained": prop.source == "safety-policy", "interlocks": fired, "flags": flags,
            "note": prop.note, "latency_ms": round(prop.latency_ms, 1), "model_risk": risk}


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
    """Runs a scenario with one operator, calling ``progress`` after each decision."""

    def __init__(self, scn: Scenario, operator: Operator, progress: Callable[[Result], None] | None = None) -> None:
        self.scn, self.operator, self.progress = scn, operator, progress
        self.res = Result(scn.key, operator.kind)
        self.plant = Plant(scn)
        self.risk: float | None = None
        self.model_choice: dict[str, str] | None = None

    def _decide(self) -> None:
        plant, index = self.plant, len(self.res.decisions)
        text = plant.state_text()
        fork = copy.deepcopy(plant)
        prop = self.operator.propose(index, plant)
        got = plant.act(prop.cls, prop.target, prop.step)
        plant.last_source = prop.source
        self.risk = prop.answers["risk"]["expected"] if prop.answers else self.risk
        self.model_choice = prop.model_choice
        self.res.forks[index] = fork
        self.res.decisions.append(_decision_row(index, plant, text, prop, got))
        if self.progress:
            self.progress(self.res)

    def _tick(self) -> None:
        plant = self.plant
        for e in plant.apply_events():
            self.res.events.append({"tick": plant.tick, "kind": e.kind, "text": e.caption})
        plant.sense()
        if plant.due():
            self._decide()
        row = plant.sample()
        row["model_risk"] = self.risk
        row["tip"] = round(rx.tip_rho(plant.core), 4)
        self.res.trace.append(row)
        plant.advance()
        fired = plant.trip()
        if fired:
            self.res.decisions.append({
                "i": len(self.res.decisions), "kind": "trip", "tick": plant.tick, "t_s": round(plant.core.t, 1),
                "fast": plant.fast(), "state_text": "", "state": plant.sample(), "answers": {},
                "model_choice": self.model_choice, "requested": None, "applied": {"cls": "scram", "target": "none", "step": "1"},
                "calls": [], "flags": [], "source": "interlock",
                "abstained": False, "interlocks": fired, "note": "automatic trip, no question asked",
                "latency_ms": 0.0, "model_risk": self.risk})

    def run(self) -> Result:
        """Run to the end of the scenario and return the rows."""
        while not self.plant.done():
            self._tick()
        self.plant.sense()
        last = self.plant.sample()
        last["model_risk"], last["tip"] = self.risk, 0.0
        self.res.trace.append(last)
        _outcomes(self.res)
        self.res.finished = True
        return self.res

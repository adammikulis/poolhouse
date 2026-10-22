"""Deciders: the decision model asked class, target and step in turn, and two scripted baselines that are not models.

Every decider takes a `SensorView` and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..controls import options as desk
from ..prompt import builder
from ..sensors.view import SensorView
from .model import Asker

ROD_CLASSES = ("insert_rod", "withdraw_rod")
REACTION_MS = 5000.0       # the scripted baselines react in 5 seconds


@dataclass
class Proposal:
    """What a decider chose: the desk action and the model answers behind it."""

    cls: str
    target: str
    step: str
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0


class ModelDecider:
    """The decision model. Each answer is the most probable option; nothing replaces, thresholds or defers it."""

    kind = "model"

    def __init__(self, asker: Asker, seed: int = 0) -> None:
        self.asker, self.seed = asker, seed

    def _round(self, n: int, calls: list[tuple[str, str, dict[str, Any]]], prop: Proposal) -> dict[str, dict[str, dict[str, Any]]]:
        out = {}
        for (key, text, _), (got, timing) in zip(calls, self.asker.ask_batch(n, calls), strict=True):
            latency = sum(item["latency_ms"] for item in got.values())
            prop.calls.append({"key": key, "state_text": text, "answers": got, "latency_ms": round(latency, 3), **timing})
            prop.answers.update({name: {**item, "call": key} for name, item in got.items()})
            prop.latency_ms += latency
            out[key] = got
        return out

    def propose(self, n: int, view: SensorView) -> Proposal:
        """Ask class (with a risk rating), then the target, then the step, each from the previous answer."""
        seed = f"{self.seed}:{n}"
        prop = Proposal("hold", desk.NONE, "1")
        prop.cls = self._round(n, [builder.decision_call(view, seed)], prop)["decision"]["class"]["choice"]
        if prop.cls in ("hold", "scram", "orm_print"):
            return prop
        options = desk.target_options(prop.cls, view, seed + ":options")
        prop.target = self._round(n, [builder.target_call(view, prop.cls, options, seed)], prop)["target"]["target"]["choice"]
        if prop.cls in ROD_CLASSES and prop.target != desk.NONE:
            prop.step = self._round(n, [builder.step_call(view, prop.cls, options[prop.target])], prop)["step"]["step"]["choice"]
        return prop


def _scripted(cls: str, target: str, step: str) -> Proposal:
    return Proposal(cls, target, step, latency_ms=REACTION_MS)


class NaiveDecider:
    """A baseline rule, not a model: chase the demand with the first listed manual rod using the average power reading."""

    kind = "naive"

    def propose(self, n: int, view: SensorView) -> Proposal:
        """Withdraw a rod when the average reading is under demand, insert when over, else make no change."""
        got = (view.readings["chA"].value + view.readings["chB"].value) / 2
        cls = "withdraw_rod" if got < view.demand_pct - 3 else "insert_rod" if got > view.demand_pct + 3 else "hold"
        if cls == "hold":
            return _scripted("hold", desk.NONE, "1")
        rods = [r for r in view.rods if r.kind == "MR" and (r.indicator_pct > 0 if cls == "withdraw_rod" else r.indicator_pct < 100)]
        return _scripted(cls, rods[0].id if rods else desk.NONE, "2")


class CrewScript:
    """The 1986 crew as a script: pull manual rods one by one while the regulator holds power, start the test on the order, press AZ-5 36 s in."""

    kind = "crew"

    def __init__(self) -> None:
        self.started: float | None = None

    def propose(self, n: int, view: SensorView) -> Proposal:
        """The scripted action for this view."""
        ordered = any("begin the turbine rundown test" in line for line in view.log)
        if view.turbine_test == "running":
            self.started = view.time_s if self.started is None else self.started
            if view.time_s - self.started >= 36:
                return _scripted("scram", desk.NONE, "1")
        elif ordered and view.turbine_test == "not running":
            return _scripted("turbine_test", "test:start", "1")
        elif view.time_s < 40 * 120:
            rods = [r for r in view.rods if r.kind == "MR" and r.indicator_pct > 0]
            if rods:
                return _scripted("withdraw_rod", max(rods, key=lambda r: r.indicator_pct).id, "2")
        return _scripted("hold", desk.NONE, "1")

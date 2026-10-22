"""The decision loop with its rows pushed to a job as they appear, and the what-if resume of a plant fork."""

from __future__ import annotations

import copy
from typing import Any

from .. import bundle, controls
from ..loop import Operator, Result, Runner
from ..operator_model import ModelOperator, Proposal
from ..plant import Plant
from ..scenarios import Scenario, variant
from .jobs import Cancelled, Job


class Sink:
    """The extra readings of one run, one entry per trace row. Plant copies share it."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def __deepcopy__(self, memo: dict[int, Any]) -> Sink:
        return self


class WebPlant(Plant):
    """A `Plant` that notes, for every trace row, the gauge readings with their ages and the rod faults."""

    def __init__(self, scn: Scenario, sink: Sink | None = None) -> None:
        super().__init__(scn)
        self.sink = sink or Sink()

    def extras(self) -> dict[str, Any]:
        """The readings the console shows, with age and fault state, and the rods whose drive is faulted."""
        c = self.core
        return {"faults": {r.id: r.fault for r in c.rods.rods if r.fault},
                "gauges": {k: [round(g.shown * g.scale, 4), round(g.age(c.t), 1), g.fault, g.age(c.t) > 2 * max(g.period, 1.0)]
                           for k, g in self.gauges.items()},
                "orm_age": round(c.t - self.orm_printed_at, 1)}

    def advance(self) -> None:
        """Note this tick's extras, then advance."""
        self.sink.rows.append(self.extras())
        super().advance()


def merged(trace: list[dict[str, Any]], sink: Sink) -> list[dict[str, Any]]:
    """The trace rows with their extras added; a trace and sink of different lengths are an error."""
    if len(trace) != len(sink.rows):
        raise ValueError(f"{len(trace)} trace rows but {len(sink.rows)} sets of extra readings")
    return [{**row, **extra} for row, extra in zip(trace, sink.rows, strict=True)]


class WebRunner(Runner):
    """A `Runner` on a `WebPlant` that emits each new row, decision, event and caption to ``job`` (when there is one)."""

    def __init__(self, scn: Scenario, operator: Operator, job: Job | None = None, tag: int = 0) -> None:
        super().__init__(scn, operator)
        self.plant = WebPlant(scn)
        self.job, self.tag, self.sent = job, tag, {"trace": 0, "decision": 0, "event": 0}
        self.skip_head = self.quiet = False
        self.captioned: set[tuple[int, str, str]] = set()

    @property
    def sink(self) -> Sink:
        """The extras of this run's plant."""
        return self.plant.sink

    def _row(self, k: int) -> dict[str, Any]:
        row = self.res.trace[k]
        return {**row, **self.sink.rows[k]} if k < len(self.sink.rows) else row

    def _push(self) -> None:
        job = self.job
        if job is None:
            return
        if self.quiet:
            job.emit(type="progress", run=self.tag, tick=self.plant.tick, ticks=self.scn.ticks)
        while not self.quiet and self.sent["trace"] < len(self.res.trace):
            job.emit(type="trace", run=self.tag, row=self._row(self.sent["trace"]))
            self.sent["trace"] += 1
        for kind, items in (("event", self.res.events), ("decision", self.res.decisions)):
            while not self.quiet and self.sent[kind] < len(items):
                job.emit(type=kind, run=self.tag, row=items[self.sent[kind]])
                self.sent[kind] += 1
        for cap in [] if self.quiet else bundle.captions(self.res):
            key = (cap["tick"], cap["kind"], cap["text"])
            if key not in self.captioned:
                self.captioned.add(key)
                job.emit(type="caption", run=self.tag, row=cap)
        model = getattr(getattr(self.operator, "asker", None), "model", "")
        if model and model != job.model:
            job.model = model
            job.emit(type="model", id=model)

    def _decide(self) -> None:
        if self.job is not None:
            self.job.emit(type="asking", run=self.tag, tick=self.plant.tick)
        super()._decide()

    def _tick(self) -> None:
        if self.job is not None and self.job.cancel.is_set():
            raise Cancelled("stopped by the person")
        if self.skip_head:
            self.skip_head = False
            self.plant.sense = lambda: None  # type: ignore[method-assign]
            self.plant.apply_events = lambda: []  # type: ignore[method-assign]
            try:
                super()._tick()
            finally:
                del self.plant.sense, self.plant.apply_events
        else:
            super()._tick()
        self._push()

    def run(self) -> Result:
        """Run to the end, pushing the last row and the finished flag."""
        res = super().run()
        self.sink.rows.append(self.plant.extras())
        self._push()
        return res


class ForcedOperator:
    """Asks the real model about the state, then applies a chosen action at the first decision only."""

    kind = "model"

    def __init__(self, inner: ModelOperator, forced: dict[str, str]) -> None:
        self.inner, self.forced, self.asker = inner, forced, inner.asker

    def propose(self, index: int, plant: Any) -> Proposal:
        """The model's own proposal, replaced by the forced action when ``index`` is 0."""
        prop = self.inner.propose(index, plant)
        if index != 0:
            return prop
        own = {"cls": prop.cls, "target": prop.target, "step": prop.step}
        prop.model_choice = prop.model_choice or own
        prop.cls, prop.target, prop.step = self.forced["cls"], self.forced["target"], self.forced["step"]
        prop.source = "forced"
        prop.note = (f"WHAT-IF: forced {prop.cls} {prop.target} (step {prop.step}); the model's own proposal was "
                     f"{own['cls']} {own['target']} (step {own['step']})")
        return prop


def check_forced(fork: Plant, forced: dict[str, Any]) -> dict[str, str]:
    """The forced (cls, target, step) when the fork's console offers it; otherwise ValueError."""
    cls, target, step = str(forced.get("cls")), str(forced.get("target")), str(forced.get("step", "2"))
    if cls not in controls.CLASSES:
        raise ValueError(f"unknown action class {cls!r}")
    options = controls.candidates(cls, fork)
    if cls != "hold" and target not in options:
        raise ValueError(f"target {target!r} is not offered for {cls}: {', '.join(options)}")
    if step not in controls.STEPS:
        raise ValueError(f"step must be one of {', '.join(controls.STEPS)}")
    return {"cls": cls, "target": "none" if cls == "hold" else target, "step": step}


def resume(fork: Plant, scn: Scenario, operator: Operator, job: Job, horizon: int | None) -> WebRunner:
    """A runner that continues the plant ``fork`` at its decision, for ``horizon`` more ticks (None: to the end)."""
    plant = copy.deepcopy(fork)
    end = scn.ticks if horizon is None else min(scn.ticks, plant.tick + horizon)
    branch = variant(scn, ticks=end, events=tuple(e for e in scn.events if e.tick != plant.tick))
    plant.scn, plant.sink = branch, Sink()
    runner = WebRunner(branch, operator, job)
    runner.plant, runner.skip_head = plant, True
    return runner


def divergence(base: list[dict[str, Any]], branch: list[dict[str, Any]], threshold: float = 0.02) -> dict[str, Any]:
    """How a what-if branch departs from the original rows over the ticks they share."""
    by_tick = {r["tick"]: r for r in base}
    both = [(by_tick[r["tick"]], r) for r in branch if r["tick"] in by_tick]
    first = next((b["tick"] for a, b in both if abs(a["P"] - b["P"]) > threshold), None)
    return {"ticks_compared": len(both), "first_divergence_tick": first,
            "max_abs_dP": round(max((abs(a["P"] - b["P"]) for a, b in both), default=0.0), 5),
            "peak_P": {"original": max((a["P"] for a, _ in both), default=None),
                       "branch": max((b["P"] for _, b in both), default=None)},
            "end_hazard": {"original": both[-1][0]["hazard"] if both else None,
                           "branch": both[-1][1]["hazard"] if both else None},
            "scram": {"original": any(a["scram"] for a, _ in both), "branch": any(b["scram"] for _, b in both)},
            "damaged": {"original": any(a["damaged"] for a, _ in both), "branch": any(b["damaged"] for _, b in both)}}

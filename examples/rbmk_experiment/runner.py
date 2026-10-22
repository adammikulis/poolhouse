"""The run: the plant steps on fixed substeps; the decider is asked back to back; each answer lands after its measured latency.

These are plain synchronous functions in simulated time, so tests and replays need no event loop. `live.LiveRunner` schedules the
same steps on a clock. At most one request is in flight. When an answer has been applied the decider reads the current view and
decides again; while it thinks the controls stay as they were.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from .controls.apply import act
from .decider.policy import CrewScript, ModelDecider, NaiveDecider
from .plant.plant import Plant
from .scenarios import Scenario
from .sensors.model import SensorModel

Decider = ModelDecider | NaiveDecider | CrewScript
MIN_DELAY_S = 0.001


@dataclass
class Result:
    """A run: true plant trace, decision rows, plant event rows, and the forks taken at each request."""

    scenario: str
    controller: str
    memory: str = "none"
    trace: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    forks: dict[int, tuple[Plant, SensorModel]] = field(default_factory=dict)
    finished: bool = False
    speed: float = 1.0
    late: list[dict[str, Any]] = field(default_factory=list)

    @property
    def decisions(self) -> list[dict[str, Any]]:
        """The rows where the decider was asked."""
        return [r for r in self.rows if r["kind"] == "decision"]


def _batch(prop: Any) -> dict[str, Any]:
    calls = [{"key": c["key"], "size": len(c["answers"]), "latency_ms": c["latency_ms"], "sent_at": c.get("sent_at"),
              "answered_at": c.get("answered_at"), "tokens_max": max((a.get("tokens", 0) for a in c["answers"].values()), default=0)}
             for c in prop.calls]
    return {"calls": calls, "questions": sum(c["size"] for c in calls), "latency_ms": round(sum(c["latency_ms"] for c in calls), 3)}


@dataclass
class RunOptions:
    """How a run is clocked and observed."""

    speed: float | None = None
    memory: str = "none"
    landing: Callable[[int], float | None] | None = None
    progress: Callable[[Result], None] | None = None


class Runner:
    """Runs a scenario with one decider."""

    def __init__(self, scn: Scenario, decider: Decider, options: RunOptions | None = None) -> None:
        opt = options or RunOptions()
        self.scn, self.decider, self.progress = scn, decider, opt.progress
        self.fixed_speed = opt.speed
        self.speed = scn.speed if opt.speed is None else opt.speed
        self.landing = opt.landing or getattr(getattr(decider, "asker", None), "landing", lambda n: None)
        self.res = Result(scn.key, decider.kind, opt.memory, speed=self.speed)
        self.plant = Plant(scn)
        self.sensors = SensorModel(scn.seed, opt.memory)
        self.sensors.print_orm(self.plant)
        self.sensors.observe(self.plant, 1.0)
        self.inflight: dict[str, Any] | None = None
        self.waiting = False
        self.requests = 0
        self.chain: Callable[[], None] | None = self._request_sync

    # ---- ticks -----------------------------------------------------------------------------------
    def begin_tick(self) -> None:
        """Scenario events for this tick."""
        for e in self.plant.apply_events():
            if e.kind == "sensor_stuck":
                self.sensors.fault("chB", e.value)
            elif e.kind == "order":
                self.sensors.note(self.plant, "supervisor order: begin the turbine rundown test")
        self._drain()

    def _drain(self) -> None:
        for e in self.plant.events:
            self.res.rows.append({**e, "kind": "plant_event", "event": e["kind"], "i": len(self.res.rows)})
        self.plant.events.clear()

    def _trace_row(self) -> None:
        view = self.sensors.view(self.plant)
        row = self.plant.truth()
        row.update(chA=view.readings["chA"].value, chB=view.readings["chB"].value, orm_printed=view.orm_printout,
                   peak_P=round(self.plant.core.peak_P, 4))
        self.res.trace.append(row)

    def finish_tick(self) -> None:
        """Sample, advance the plant one tick (an answer that lands inside it is applied there, and the next request starts), run the protections."""
        plant = self.plant
        self._trace_row()
        seconds, step = plant.regime()
        remaining = seconds
        while remaining > 1e-9:
            first = None if self.inflight is None else self.inflight["due_s"] - plant.core.t
            if first is not None and first < remaining:
                first = max(first, 0.0)
                plant.substeps(first, step)
                remaining -= first
                self._land()
                if self.chain:
                    self.chain()
            else:
                plant.substeps(remaining, step)
                remaining = 0.0
        plant.end_tick(seconds)
        self.sensors.observe(plant, seconds)
        self._drain()

    # ---- requests --------------------------------------------------------------------------------
    def wants_request(self) -> bool:
        """The decider decides again as soon as nothing is in flight."""
        return self.inflight is None and not self.waiting and not self.plant.core.damaged

    def request(self) -> tuple[int, Any, dict[str, Any], tuple[Plant, SensorModel]]:
        """Take the snapshot for a request: its number, the view, the snapshot row and the fork."""
        plant = self.plant
        n, self.requests = self.requests, self.requests + 1
        view = self.sensors.view(plant)
        snapshot = {"view": asdict(view), "tick": plant.tick, "t": plant.core.t, "t_s": round(plant.core.t, 1),
                    "fast": plant.fast()}
        self.waiting = True
        return n, view, snapshot, (copy.deepcopy(plant), copy.deepcopy(self.sensors))

    def receive(self, n: int, snapshot: dict[str, Any], fork: tuple[Plant, SensorModel], prop: Any) -> None:
        """An answer has arrived: log the row and land it at snapshot time plus latency times speed."""
        plant = self.plant
        self.waiting = False
        speed = self.fixed_speed or (self.speed if snapshot["fast"] else self.scn.quiet_speed)
        delay = max(prop.latency_ms / 1000.0 * speed, MIN_DELAY_S)
        self.res.forks[n] = fork
        row = {"kind": "decision", "i": len(self.res.rows), "n": n, "tick": snapshot["tick"], "t_s": snapshot["t_s"],
               "view": snapshot["view"], "calls": prop.calls, "answers": prop.answers,
               "choice": {"cls": prop.cls, "target": prop.target, "step": prop.step}, "batch": _batch(prop),
               "latency_ms": round(prop.latency_ms, 1), "delay_s": round(delay, 3), "applied_tick": None,
               "applied_t_s": None, "log_line": None}
        self.res.rows.append(row)
        due = self.landing(n)
        due = snapshot["t"] + delay if due is None else due
        row["due_s"] = max(due, plant.core.t)
        self.inflight = {"prop": prop, "row": row, "due_s": row["due_s"]}

    def _land(self) -> None:
        plant, fl = self.plant, self.inflight
        if fl is None:
            raise RuntimeError("no action is in flight")
        prop, row = fl["prop"], fl["row"]
        line = act(prop.cls, prop.target, prop.step, plant, self.sensors)
        if line:
            self.sensors.note(plant, line)
        row.update(applied_tick=plant.tick, applied_t_s=round(plant.core.t, 1), log_line=line or None)
        self.inflight = None
        self._drain()
        if self.progress:
            self.progress(self.res)

    def _request_sync(self) -> None:
        if self.wants_request():
            n, view, snapshot, fork = self.request()
            self.receive(n, snapshot, fork, self.decider.propose(n, view))

    # ---- the run -----------------------------------------------------------------------------------
    def finish_run(self) -> Result:
        """Close the run with a final trace row."""
        self._trace_row()
        self.res.finished = True
        return self.res

    def run(self) -> Result:
        """Run to the end of the scenario and return the rows."""
        while not self.plant.done():
            self.begin_tick()
            if self.chain:
                self.chain()
            self.finish_tick()
        return self.finish_run()

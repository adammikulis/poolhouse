"""The sensor model: the only reader of the plant on the model's side of the chain.

It turns true plant values into instrument readings with lag, noise, sampling and faults, keeps the desk log and the
ORM printout, and produces the immutable `SensorView`. Nothing else leaves the plant for the decision model.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass
from typing import Any

from ..plant import reactor as rx
from .view import PumpDesk, Reading, RodDesk, SensorView

TREND_POINTS = 12
TREND_EVERY_S = 60.0
LOG_LINES = 5
TRENDED = ("chA", "flow", "press_l", "level_l")


@dataclass
class Gauge:
    """A sampled, lagged, noisy reading. ``fault`` is "" | stuck."""

    period: float
    tau: float
    noise: float
    lagged: float | None = None
    shown: float = 0.0
    taken: float = -1e9
    fault: str = ""
    stuck_at: float = 0.0

    def update(self, t: float, dt: float, true: float, rng: random.Random) -> None:
        """Advance the lag by ``dt`` and take a new sample when the sampling period has passed."""
        self.lagged = true if self.lagged is None or self.tau <= 0 else true + (self.lagged - true) * math.exp(-dt / self.tau)
        jitter = rng.gauss(0.0, self.noise)
        if t - self.taken >= self.period:
            self.shown, self.taken = self.lagged * (1.0 + jitter), t
        if self.fault == "stuck":
            self.shown = self.stuck_at


GAUGES = {
    "chA": (1.0, 1.0, 0.01), "chB": (1.0, 1.0, 0.01), "heat": (30.0, 120.0, 0.005), "flow": (5.0, 5.0, 0.01),
    "level_l": (2.0, 5.0, 0.0), "level_r": (2.0, 5.0, 0.0), "press_l": (2.0, 5.0, 0.002), "press_r": (2.0, 5.0, 0.002),
    "header": (2.0, 8.0, 0.002), "feedwater": (5.0, 10.0, 0.01),
}
SCALE = {"chA": 100.0, "chB": 100.0, "heat": 100.0, "flow": 100.0, "feedwater": 100.0}


def _truth(plant: Any) -> dict[str, float]:
    c = plant.core
    heat = 0.94 * c.P + c.D
    return {"chA": c.P, "chB": c.P, "heat": heat, "flow": c.f, "level_l": plant.level, "level_r": plant.level * 0.9 - 1.0,
            "press_l": c.p, "press_r": c.p * 0.995, "header": c.p * 0.97, "feedwater": heat * (1.0 + 0.1 * (c.f - 1.0))}


class SensorModel:
    """Instrument lag, noise, sampling and faults; the ORM printout; the desk log; the strip-recorder trends."""

    def __init__(self, seed: int, memory: str = "none") -> None:
        self.rng = random.Random(seed)  # noqa: S311
        self.gauges = {name: Gauge(*spec) for name, spec in GAUGES.items()}
        self.memory = memory
        self.orm_value, self.orm_t = 0.0, 0.0
        self.log: list[str] = []
        self.trend: dict[str, deque[float]] = {n: deque(maxlen=TREND_POINTS) for n in TRENDED}
        self.last_trend = -1e9

    def fault(self, gauge: str, value: float) -> None:
        """A gauge sticks at ``value``."""
        self.gauges[gauge].fault, self.gauges[gauge].stuck_at = "stuck", value

    def print_orm(self, plant: Any) -> None:
        """The ORM computer prints the margin as it is now."""
        self.orm_value, self.orm_t = plant.core.orm(), plant.core.t

    def note(self, plant: Any, text: str) -> None:
        """A plain line in the desk log."""
        clock = plant.core.t
        self.log = [*self.log, f"{int(clock // 3600):02d}:{int(clock % 3600 // 60):02d}:{int(clock % 60):02d} {text}"][-LOG_LINES:]

    def observe(self, plant: Any, dt: float) -> SensorView:
        """Update every gauge over the last ``dt`` seconds and return what the room shows now."""
        c = plant.core
        for name, true in _truth(plant).items():
            self.gauges[name].update(c.t, dt, true, self.rng)
        if self.memory == "history" and c.t - self.last_trend >= TREND_EVERY_S:
            self.last_trend = c.t
            for name in TRENDED:
                self.trend[name].append(round(self.gauges[name].shown * SCALE.get(name, 1.0), 2))
        return self.view(plant)

    def view(self, plant: Any) -> SensorView:
        """The room as it is now, without advancing any gauge."""
        c = plant.core
        readings = {n: Reading(round(g.shown * SCALE.get(n, 1.0), 3), round(max(c.t - g.taken, 0.0), 1))
                    for n, g in self.gauges.items()}
        rods = tuple(RodDesk(r.id, r.kind, round(r.r * 100), round(r.target * 100)) for r in c.rods.rods)
        pumps = tuple(PumpDesk(i + 1, c.pump_on[i], round(c.pump_s[i] * 100)) for i in range(rx.PUMPS))
        test = {"pending": "not running", "none": "not running", "running": "running", "aborted": "aborted"}[plant.test]
        return SensorView(
            time_s=round(c.t, 1), tick=plant.tick, demand_pct=round(plant.demand * 100, 1), readings=readings,
            orm_printout=round(self.orm_value, 1), orm_age_s=round(c.t - self.orm_t, 1), rods=rods, pumps=pumps,
            regulator_on=plant.ar_on,
            regulator_setpoint_pct=round(plant.reg_setpoint * 100, 1), switches=dict(plant.prot), turbine_test=test,
            az5=c.scram, log=tuple(self.log),
            trends={n: tuple(d) for n, d in self.trend.items()} if self.memory == "history" else {})

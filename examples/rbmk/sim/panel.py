"""The instrument panel: lagged, noisy, sampled gauges and the text each sub-panel shows the decider."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from . import reactor as rx


@dataclass
class Gauge:
    """A sampled, lagged, noisy reading. ``fault`` is "" | stuck | dead."""

    label: str
    unit: str
    period: float
    tau: float
    noise: float
    scale: float = 1.0
    lagged: float | None = None
    shown: float = 0.0
    taken: float = 0.0
    fault: str = ""
    stuck_at: float = 0.0

    def update(self, t: float, dt: float, true: float, rng: random.Random) -> None:
        """Advance the lag by ``dt`` and take a new sample when the sampling period has passed."""
        self.lagged = true if self.lagged is None or self.tau <= 0 else (
            true + (self.lagged - true) * math.exp(-dt / self.tau))
        jitter = rng.gauss(0.0, self.noise)
        if t - self.taken >= self.period or self.taken == 0.0:
            self.shown, self.taken = self.lagged * (1.0 + jitter), t
        if self.fault in ("stuck", "dead"):
            self.shown = self.stuck_at

    def age(self, t: float) -> float:
        """Seconds since the shown value was sampled."""
        return max(t - self.taken, 0.0)

    def text(self, t: float, digits: int = 1) -> str:
        """The reading with its unit and age; flags a reading that is old for its sampling period."""
        age = self.age(t)
        flag = ", STALE" if age > 2 * max(self.period, 1.0) else ""
        return f"{self.shown * self.scale:.{digits}f}{self.unit} ({age:.0f} s old{flag})"


def make_gauges() -> dict[str, Gauge]:
    """The continuous instruments of the toy panel."""
    return {
        "chA": Gauge("Power channel A (lateral ionisation chamber)", "%", 1.0, 1.0, 0.01, 100.0),
        "chB": Gauge("Power channel B (lateral ionisation chamber)", "%", 1.0, 1.0, 0.01, 100.0),
        "heat": Gauge("Thermal power by heat balance", "%", 30.0, 120.0, 0.005, 100.0),
        "flow": Gauge("Core coolant flow meters", "%", 5.0, 5.0, 0.01, 100.0),
        "fuel": Gauge("Hottest fuel channel temperature", " C", 10.0, 8.0, 0.01),
        "graphite": Gauge("Hottest graphite temperature", " C", 60.0, 1200.0, 0.005),
        "level_l": Gauge("Left drum level", " cm", 2.0, 5.0, 0.0),
        "level_r": Gauge("Right drum level", " cm", 2.0, 5.0, 0.0),
        "press_l": Gauge("Left drum pressure", " MPa", 2.0, 5.0, 0.002),
        "press_r": Gauge("Right drum pressure", " MPa", 2.0, 5.0, 0.002),
        "header": Gauge("Main steam header pressure", " MPa", 2.0, 8.0, 0.002),
        "feedwater": Gauge("Feedwater flow", "%", 5.0, 10.0, 0.01, 100.0),
        "subcool": Gauge("Core inlet subcooling margin", " K", 30.0, 30.0, 0.03),
    }


def true_values(plant: Any) -> dict[str, float]:
    """What each gauge would read if it were perfect, from the plant's true state."""
    c = plant.core
    heat = 0.94 * c.P + c.D
    return {"chA": c.P, "chB": c.P, "heat": heat, "flow": c.f, "fuel": c.Tf,
            "graphite": rx.TC + 330.0 * heat, "level_l": plant.level, "level_r": plant.level * 0.9 - 1.0,
            "press_l": c.p, "press_r": c.p * 0.995, "header": c.p * 0.97,
            "feedwater": heat * (1.0 + 0.1 * (c.f - 1.0)), "subcool": 28.0 * c.f / (0.2 + heat)}


def sense(plant: Any, dt: float) -> None:
    """Update every gauge from the plant's true state over the last ``dt`` seconds."""
    truth = true_values(plant)
    for name, g in plant.gauges.items():
        g.update(plant.core.t, dt, truth[name], plant.rng)
    a, b = plant.gauges["chA"].shown, plant.gauges["chB"].shown
    plant.reading = (round(max(a, 0.0), 4), round(max(b, 0.0), 4))
    plant.history = ([*plant.history, sum(plant.reading) / 2])[-3:]
    if plant.core.t - plant.orm_taken >= plant.orm_period:
        plant.print_orm()


def _pumps(plant: Any) -> str:
    c = plant.core
    return "; ".join(f"pump {i + 1} {'running ' + format(c.pump_s[i] * 100, '.0f') + '%' if c.pump_on[i] else 'stopped'}"
                     for i in range(rx.PUMPS))


def neutron(plant: Any) -> str:
    """The neutron panel text."""
    g, t = plant.gauges, plant.core.t
    a, b = plant.reading
    notes = []
    if abs(a - b) > 0.15:
        notes.append(f"NOTE: channels A and B disagree by {abs(a - b) * 100:.0f} points.")
    avg = sum(plant.reading) / 2
    if avg < 0.01:
        notes.append("NOTE: below 1% of nominal the power-range chambers are off scale; read the start range.")
    heat = g["heat"].shown
    if abs(heat - avg) > 0.25 * max(avg, 0.02) + 0.03 and avg > 0.02:
        notes.append(f"NOTE: heat-balance power {heat * 100:.0f}% and chambers {avg * 100:.0f}% differ (the heat balance lags).")
    trend = ", ".join(f"{h * 100:.0f}%" for h in plant.history) or "no history"
    return (f"NEUTRON PANEL, tick {plant.tick}. Demand: {plant.setpoint * 100:.0f}% of nominal power "
            f"({plant.setpoint * rx.THERMAL_MW:.0f} MW thermal of {rx.THERMAL_MW:.0f}). "
            f"Power channel A reads {a * 100:.1f}% ({g['chA'].age(t):.0f} s old), channel B reads {b * 100:.1f}% "
            f"({g['chB'].age(t):.0f} s old). Heat-balance power: {g['heat'].text(t, 0)}. "
            f"Recent average chamber readings: {trend}. Radial flux tilt, centre over edge: "
            f"{plant.tilt_reading():.2f}. Range switch: {'start range' if avg < 0.01 else 'power range'}. "
            + " ".join(notes))


def rods(plant: Any) -> str:
    """The rod and margin panel text."""
    r = plant.core.rods
    man = r.group("MR")
    ar = r.group("AR")
    faults = [x for x in r.rods if x.fault or abs(x.r - x.target) > 0.01]
    lines = [f"RODS PANEL. Operational reactivity margin (ORM), computer printout: {plant.orm_text()}. "
             f"The printout is slow and is requested by hand. The minimum permitted ORM is {rx.ORM_MIN:.0f} rod-equivalents. "
             f"Manual rods: {sum(1 for x in man if x.r > 0.02)} of {len(man)} inserted at all, "
             f"{sum(1 for x in man if x.r > 0.5)} more than half. Automatic regulation rods average "
             f"{100 * sum(x.r for x in ar) / len(ar):.0f}% inserted. Shortened absorbers average "
             f"{100 * sum(x.r for x in r.group('USP')) / 24:.0f}% inserted. Emergency protection rods average "
             f"{100 * sum(x.r for x in r.group('EP')) / 24:.0f}% inserted. "
             f"Automatic regulator is {'ON' if plant.ar_on else 'OFF'}, setpoint {plant.setpoint * 100:.0f}%."]
    for x in faults[:4]:
        lines.append(f"NOTE: rod {x.id} indicator {x.r * 100:.0f}% but commanded {x.target * 100:.0f}%"
                     + (f" ({x.fault} drive)" if x.fault else " (moving)") + ".")
    return " ".join(lines)


def coolant(plant: Any) -> str:
    """The coolant and pump panel text."""
    g, t, c = plant.gauges, plant.core.t, plant.core
    notes = ""
    if abs(g["flow"].shown - c.f) > 0.1:
        notes = f" NOTE: flow meters read {g['flow'].shown * 100:.0f}% but the pump speeds add up to {c.f * 100:.0f}%."
    return (f"COOLANT PANEL. Main circulation pumps: {_pumps(plant)}. Throttle valves {c.throttle * 100:.0f}% open. "
            f"Core flow meters: {g['flow'].text(t, 0)}. Core inlet subcooling margin: {g['subcool'].text(t, 0)}. "
            f"Hottest fuel channel temperature: {g['fuel'].text(t, 0)} (limit {rx.TF_LIMIT:.0f}). "
            f"Hottest graphite temperature: {g['graphite'].text(t, 0)}. "
            f"Computer estimate of steam void fraction: {c.alpha * 100:.0f}% (nominal 25%)." + notes)


def steam(plant: Any) -> str:
    """The drums, steam and turbine panel text."""
    g, t = plant.gauges, plant.core.t
    test = {"pending": "NOT STARTED, about to begin", "running": "RUNNING, turbine rundown, pumps 5 to 8 on the coasting generator",
            "aborted": "ABORTED", "none": "no test planned"}[plant.test]
    return (f"STEAM AND TURBINE PANEL. Drum levels: left {g['level_l'].text(t, 0)}, right {g['level_r'].text(t, 0)}. "
            f"Drum pressures: left {g['press_l'].text(t, 2)}, right {g['press_r'].text(t, 2)} (limit {rx.P_LIMIT:.1f}). "
            f"Main steam header {g['header'].text(t, 2)}. Feedwater flow {g['feedwater'].text(t, 0)}. "
            f"Turbine steam admission {'closed' if plant.test == 'running' else 'open'}; trip valve "
            f"{'closed' if plant.tg_disconnected else 'open'}; turbogenerator 8 {'disconnected' if plant.tg_disconnected else 'connected'}. "
            f"Turbine rundown test: {test}.")


def protections(plant: Any) -> str:
    """The protection switches and alarm annunciator text."""
    states = "; ".join(f"{k}: {'ACTIVE' if on else 'DEFEATED'}" for k, on in plant.prot.items())
    return (f"PROTECTION PANEL. {states}. Emergency shutdown AZ-5: {'ACTIVE' if plant.core.scram else 'not pressed'}. "
            f"Alarm annunciator: {'; '.join(plant.alarms()) or 'no alarms lit'}.")


PANELS = {"neutron": neutron, "rods": rods, "coolant": coolant, "steam": steam, "protection": protections}


def full(plant: Any) -> str:
    """Every sub-panel one after another, with the clock."""
    c = plant.core
    clock = f"{int(c.t // 3600)}h{int(c.t % 3600 // 60):02d}m{int(c.t % 60):02d}s"
    return (f"RBMK-type reactor console (educational toy), plant time {clock}. Last operator action: {plant.last_action}. "
            + " ".join(f(plant) for f in PANELS.values()))

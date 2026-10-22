"""The plant: hidden true state, physics on fixed substeps, scenario events, desk operations and the plant's own protections.

Nothing outside `sensors` and `controls.apply` reads this object; the decision model never sees it.
"""

from __future__ import annotations

import math

from .. import scenarios
from ..scenarios import Event, Scenario
from . import reactor as rx

POWER_TRIP = 1.2           # relative power
DRUM_PRESSURE_TRIP = 8.0   # MPa
DRUM_LEVEL_TRIP = 45.0     # cm from the level setpoint
REG_GAIN = 2.8e-4
STEADY_TICKS = 2           # consecutive ticks over which power is constant within the power channels' noise (1 percent, sensors/model.py; unverified): two is the fewest that give a rate
STEADY_TOLERANCE = 0.01
SOURCE_FLOOR = 0.001      # relative power below which a change is not read as a change


class Plant:
    """A seeded plant with its scenario applied tick by tick. Copyable, so a what-if can fork it."""

    def __init__(self, scn: Scenario) -> None:
        self.scn = scn
        self.core = scn.build_core()
        self.tick = 0
        self.demand = scn.setpoint
        self.reg_setpoint = scn.setpoint
        self.ar_on = scn.ar_on
        self.prot = {k: k not in scn.defeated for k in PROTECTION_KEYS}
        self.test = "pending" if scn.has_test else "none"
        self.tg_disconnected = False
        self.level = 0.0
        self.stuck_gauges: dict[str, float] = {}
        self.events: list[dict[str, object]] = []
        self.fast_since: float | None = None
        self.started_below = scn.orm is not None and scn.orm < rx.ORM_MIN
        self.steady = 0
        self._last_power = self.core.P

    # ---- clock ----------------------------------------------------------------------------------
    def fast(self) -> bool:
        """Whether the plant is in the 1 second tick regime."""
        c = self.core
        if self.scn.fast_from >= 0 and self.tick >= self.scn.fast_from:
            if self.fast_since is None:
                self.fast_since = c.t
            if c.t - self.fast_since < self.scn.fast_window_s:
                return True
        moving = any(abs(p - float(on)) > 0.05 for p, on in zip(c.pump_s, c.pump_on, strict=True))
        return (c.scram and c.scram_t < 120.0) or c.P > 1.1 or c.rho > 0.2 or moving

    def regime(self) -> tuple[float, float]:
        """The tick length and the fixed substep, in seconds, for the regime the plant is in now."""
        return (scenarios.FAST_TICK_S, 0.5) if self.fast() else (self.scn.slow_s, 10.0)

    def log(self, kind: str, **fields: object) -> None:
        """A plant event: something the plant did or ignored."""
        self.events.append({"tick": self.tick, "t_s": round(self.core.t, 1), "kind": kind, **fields})

    # ---- scenario events -------------------------------------------------------------------------
    def apply_events(self) -> list[Event]:
        """Carry out the plant events scheduled for this tick; sensor events are returned for the sensor model."""
        due = [e for e in self.scn.events if e.tick == self.tick]
        for e in due:
            self._event(e)
        return due

    def _event(self, e: Event) -> None:
        c = self.core
        if e.kind == "setpoint":
            self.demand = e.value
        elif e.kind == "pump_trip":
            c.pump_on[int(e.value) - 1], c.pump_tau[int(e.value) - 1] = False, 12.0
        elif e.kind == "ar_off":
            self.ar_on = False
        elif e.kind in ("rod_stuck", "rod_slow"):
            pool = sorted((r for r in c.rods.group("MR") if r.r > 0.02), key=lambda r: -r.w)
            pool[int(e.value)].fault = "stuck" if e.kind == "rod_stuck" else "slow"
        elif e.kind == "sensor_stuck":
            self.stuck_gauges["chB"] = e.value
        elif e.kind != "order":
            raise ValueError(f"unknown event kind {e.kind!r}")
        self.log("scenario_event", scenario_kind=e.kind, value=e.value)

    # ---- desk operations (called by controls.apply) -------------------------------------------------
    def start_test(self) -> None:
        """Turbine rundown test: steam admission closes, pumps 5 to 8 run on the coasting generator."""
        self.test, self.tg_disconnected = "running", True
        for i in range(4, 8):
            self.core.pump_on[i], self.core.pump_tau[i] = False, rx.TAU_COAST

    def abort_test(self) -> None:
        """Abort the test: the four pumps get grid power back."""
        self.test, self.tg_disconnected = "aborted", False
        for i in range(4, 8):
            self.core.pump_on[i], self.core.pump_tau[i] = True, 10.0

    def scram(self) -> None:
        """AZ-5."""
        rx.start_scram(self.core)

    # ---- stepping -----------------------------------------------------------------------------------
    def _regulate(self, dt: float) -> None:
        c = self.core
        if not self.ar_on or c.scram:
            return
        seen = c.P
        for gauge, value in self.stuck_gauges.items():
            if gauge == "chB":
                seen = (c.P + value) / 2
        err = (self.reg_setpoint - seen) / max(self.reg_setpoint, 0.05)
        regs = c.rods.group("AR")
        mean = sum(r.r for r in regs) / len(regs)
        c.rods.command(regs, min(max(mean - REG_GAIN * err * dt, 0.1), 0.85), "regulator")

    def _steam(self, dt: float) -> None:
        c = self.core
        swell = 8.0 * (c.alpha - rx.VOID_NOM) / rx.VOID_NOM - 10.0 * max(0.0, 1.0 - c.f)
        self.level += (swell - self.level) * (1.0 - math.exp(-dt / 20.0))

    def substeps(self, seconds: float, step: float) -> None:
        """Advance ``seconds`` in fixed substeps of at most ``step`` seconds."""
        n = math.ceil(seconds / step) if seconds > 0 else 0
        for _ in range(n):
            self._regulate(seconds / n)
            rx.substep(self.core, seconds / n)
            self._steam(seconds / n)

    def end_tick(self) -> None:
        """Count the tick, run the plant's protections and note whether the unit is steady with its margin restored."""
        self.tick += 1
        self._protect()
        c = self.core
        calm = abs(c.P - self._last_power) <= STEADY_TOLERANCE * max(c.P, SOURCE_FLOOR)
        self._last_power = c.P
        restored = self.started_below and c.orm() >= rx.ORM_MIN and not c.scram and not c.damaged
        self.steady = self.steady + 1 if restored and calm else 0

    def _protect(self) -> None:
        c = self.core
        if c.scram:
            return
        found = []
        if c.P > POWER_TRIP:
            found.append("POWER_HIGH")
        if self.prot["DRUM_TRIPS"] and (c.p > DRUM_PRESSURE_TRIP or abs(self.level) > DRUM_LEVEL_TRIP):
            found.append("DRUM_TRIPS")
        if self.prot["TG_TRIP"] and self.tg_disconnected:
            found.append("TG_TRIP")
        for name in found:
            self.log("protection_trip", protection=name)
        if found:
            rx.start_scram(c)

    def done(self) -> bool:
        """The run is over at one of three outcomes: fuel damaged, the unit shut down (AZ-5 and every free rod fully in), or the margin
        restored with power steady. A scenario's own tick count or time bound (`max_s`, with its source beside it) also ends it."""
        c = self.core
        if self.tick >= self.scn.ticks or (self.scn.max_s is not None and c.t >= self.scn.max_s):
            return True
        if c.damaged:
            return True
        if c.scram and all(r.r >= 1.0 for r in c.rods.rods if r.fault != "stuck"):
            return True
        return self.steady >= STEADY_TICKS

    def truth(self) -> dict[str, object]:
        """One row of true plant values for the recording's trace (display only; never shown to the model)."""
        c = self.core
        return {"tick": self.tick, "t_s": round(c.t, 1), "dt": scenarios.FAST_TICK_S if self.fast() else self.scn.slow_s,
                "P": round(c.P, 5), "X": round(c.X, 4), "I": round(c.iod, 4), "void": round(c.alpha, 4),
                "orm": round(c.orm(), 3), "f": round(c.f, 4), "Tf": round(c.Tf, 1), "p": round(c.p, 3),
                "rho": round(c.rho, 4), "level": round(self.level, 2), "demand": self.demand, "scram": c.scram,
                "damaged": c.damaged, "pumps": [round(s, 3) for s in c.pump_s], "rods": [round(r.r * 100) for r in c.rods.rods],
                "throttle": round(c.throttle, 3), "ar_on": self.ar_on, "test": self.test, "prot": dict(self.prot),
                "tip": round(rx.tip_rho(c), 4)}


PROTECTION_KEYS = ("DRUM_TRIPS", "TG_TRIP", "ECCS")

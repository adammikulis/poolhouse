"""The plant a scenario runs on: reactor, protections, turbine test, instruments, events and the console."""

from __future__ import annotations

import math
import random

from . import controls, panel, reactor as rx, scenarios
from .scenarios import Event, Scenario


class Plant:
    """A seeded plant with its scenario applied tick by tick. Copyable, so a what-if can fork it."""

    def __init__(self, scn: Scenario) -> None:
        self.scn = scn
        self.core = scn.build_core()
        self.rng = random.Random(scn.seed)  # noqa: S311
        self.tick = 0
        self.setpoint = scn.setpoint
        self.ar_on = scn.ar_on
        self.prot = {k: k not in scn.defeated for k in controls.PROTECTIONS}
        self.test = "pending" if scn.has_test else "none"
        self.tg_disconnected = False
        self.level = 0.0
        self.gauges = panel.make_gauges()
        self.reading = (self.core.P, self.core.P)
        self.history: list[float] = []
        self.last_action = "none yet"
        self.last_source = ""
        self.since_decision = scenarios.DECIDE_SLOW
        self.orm_period, self.orm_taken = 900.0, -1e9
        self.orm_value, self.xenon_value = 0.0, 0.0
        self.orm_printed_at = 0.0
        self.fired_events: list[str] = []
        panel.sense(self, 1.0)
        self.print_orm()

    # ---- instruments -------------------------------------------------------------------------
    def sense(self) -> None:
        """Take this tick's readings."""
        panel.sense(self, 0.0)

    def print_orm(self) -> None:
        """The ORM computer prints the margin and a xenon estimate as they are now."""
        self.orm_value, self.xenon_value = self.core.orm(), self.core.X
        self.orm_taken = self.orm_printed_at = self.core.t

    def orm_text(self) -> str:
        """The printout with its age."""
        age = self.core.t - self.orm_printed_at
        return (f"{self.orm_value:.1f} rod-equivalents, xenon estimate {self.xenon_value:.2f}, "
                f"printed {age / 60:.0f} min ago")

    def tilt_reading(self) -> float:
        """The centre-over-edge flux ratio the in-core detectors report."""
        return 1.0 + 0.9 * self.core.rods.tilt

    def alarms(self) -> list[str]:
        """The annunciator windows that are lit."""
        c, out = self.core, []
        if c.orm() < rx.ORM_MIN:
            out.append("ORM BELOW MINIMUM")
        if c.f < 0.8:
            out.append("LOW CORE FLOW")
        if abs(self.reading[0] - self.reading[1]) > 0.15:
            out.append("POWER CHANNELS DISAGREE")
        if c.p > 7.6:
            out.append("HIGH DRUM PRESSURE")
        if c.rho > 0.1:
            out.append("POWER RISING")
        out += [f"PROTECTION DEFEATED: {k}" for k, on in self.prot.items() if not on]
        out += [f"ROD DRIVE FAULT {r.id}" for r in c.rods.rods if r.fault]
        return out

    # ---- turbine test --------------------------------------------------------------------------
    def start_test(self) -> None:
        """Turbine rundown test: steam admission closes, pumps 5 to 8 run on the coasting generator."""
        self.test, self.tg_disconnected = "running", True
        for i in range(4, 8):
            self.core.pump_on[i], self.core.pump_tau[i] = False, 25.0

    def abort_test(self) -> None:
        """Abort the test: the four pumps get grid power back."""
        self.test, self.tg_disconnected = "aborted", False
        for i in range(4, 8):
            self.core.pump_on[i], self.core.pump_tau[i] = True, 10.0

    # ---- scenario ----------------------------------------------------------------------------
    def fast(self) -> bool:
        """Whether the plant is in the 1 second tick regime."""
        c = self.core
        if self.scn.fast_from >= 0 and self.tick >= self.scn.fast_from and not (c.scram and c.scram_t > 120.0):
            return True
        moving = any(abs(p - float(on)) > 0.05 for p, on in zip(c.pump_s, c.pump_on, strict=True))
        return (c.scram and c.scram_t < 120.0) or c.P > 1.1 or c.rho > 0.2 or moving

    def apply_events(self) -> list[Event]:
        """Carry out the events scheduled for this tick."""
        due = [e for e in self.scn.events if e.tick == self.tick]
        for e in due:
            self._event(e)
            self.fired_events.append(e.caption)
        return due

    def _event(self, e: Event) -> None:
        c = self.core
        if e.kind == "setpoint":
            self.setpoint = e.value
        elif e.kind == "pump_trip":
            c.pump_on[int(e.value) - 1], c.pump_tau[int(e.value) - 1] = False, 12.0
        elif e.kind == "test_start":
            if self.test == "pending":
                self.start_test()
        elif e.kind == "sensor_stuck":
            g = self.gauges["chB"]
            g.fault, g.stuck_at = "stuck", e.value
        elif e.kind in ("rod_stuck", "rod_slow"):
            pool = sorted((r for r in c.rods.group("MR") if r.r > 0.02), key=lambda r: -r.w)
            pool[int(e.value)].fault = "stuck" if e.kind == "rod_stuck" else "slow"
        else:
            raise ValueError(f"unknown event kind {e.kind!r}")

    # ---- stepping --------------------------------------------------------------------------------
    def _regulate(self, dt: float) -> None:
        if not self.ar_on or self.core.scram:
            return
        b = self.gauges["chB"]
        seen = (self.core.P + (b.stuck_at if b.fault else self.core.P)) / 2
        err = (self.setpoint - seen) / max(self.setpoint, 0.05)
        regs = self.core.rods.group("AR")
        mean = sum(r.r for r in regs) / len(regs)
        want = min(max(mean - 2.8e-4 * err * dt, 0.1), 0.85)
        self.core.rods.command(regs, want, "regulator")

    def _steam(self, dt: float) -> None:
        c = self.core
        swell = 8.0 * (c.alpha - rx.VOID_NOM) / rx.VOID_NOM - 10.0 * max(0.0, 1.0 - c.f)
        self.level += (swell - self.level) * (1.0 - math.exp(-dt / 20.0))

    def advance(self) -> None:
        """One tick: 5 minutes in the slow regime, 1 second in the fast one."""
        seconds, step = (scenarios.FAST_TICK_S, 0.5) if self.fast() else (scenarios.SLOW_TICK_S, 10.0)
        n = max(1, math.ceil(seconds / step))
        for _ in range(n):
            self._regulate(seconds / n)
            rx.substep(self.core, seconds / n)
            self._steam(seconds / n)
        self.tick += 1
        self.since_decision += 1
        panel.sense(self, seconds)

    def due(self) -> bool:
        """Whether the operator is asked this tick."""
        every = scenarios.DECIDE_FAST if self.fast() else scenarios.DECIDE_SLOW
        return self.since_decision >= (self.scn.decide_slow if not self.fast() else every) and not self.core.scram and not self.core.damaged

    def act(self, cls: str, target: str, step: str) -> controls.Gated:
        """Pass a chosen action through the interlocks and carry out what is allowed."""
        got = controls.gate(cls, target, step, self)
        controls.apply(got, self)
        self.last_action = f"{got.cls} {got.target}".strip() if got.cls != "hold" else "hold"
        self.since_decision = 0
        return got

    def trip(self) -> list[dict[str, str]]:
        """Run the automatic trips; a trip starts the shutdown."""
        found = controls.trips(self)
        if found:
            rx.start_scram(self.core)
        return found

    def done(self) -> bool:
        """Whether the run is over: out of ticks, damaged, or shut down and settled."""
        c = self.core
        if self.tick >= self.scn.ticks or (c.damaged and c.scram_t > 5.0):
            return True
        return c.scram and c.scram_t > 600.0 and c.P < 0.03

    def state_text(self) -> str:
        """The whole console the operator model reads."""
        return panel.full(self)

    def sample(self) -> dict[str, object]:
        """One trace row of what the plant is doing now (true values, plus both channels)."""
        c = self.core
        a, b = self.reading
        return {"tick": self.tick, "t_s": round(c.t, 1),
                "dt": scenarios.FAST_TICK_S if self.fast() else scenarios.SLOW_TICK_S,
                "P": round(c.P, 5), "chA": a, "chB": b, "X": round(c.X, 4), "I": round(c.iod, 4),
                "void": round(c.alpha, 4), "orm": round(c.orm(), 3), "orm_printed": round(self.orm_value, 2),
                "f": round(c.f, 4), "Tf": round(c.Tf, 1), "p": round(c.p, 3), "rho": round(c.rho, 4),
                "level": round(self.level, 2), "setpoint": self.setpoint, "hazard": rx.hazard(c),
                "scram": c.scram, "damaged": c.damaged, "pumps": [round(s, 3) for s in c.pump_s],
                "rods": [round(r.r * 100) for r in c.rods.rods], "throttle": round(c.throttle, 3),
                "ar_on": self.ar_on, "test": self.test, "prot": dict(self.prot)}

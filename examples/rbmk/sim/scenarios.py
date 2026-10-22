"""The scenarios: a start state, a demand schedule and timed events, all fixed by a seed."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from . import reactor as rx

SLOW_TICK_S = 300.0
FAST_TICK_S = 1.0
DECIDE_SLOW, DECIDE_FAST = 3, 5
ALL_PROTECTIONS = ("ORM_RULE", "DRUM_TRIPS", "TG_TRIP", "ECCS")


@dataclass(frozen=True)
class Event:
    """Something that happens to the plant at a tick: no decision chose it."""

    tick: int
    kind: str          # setpoint | pump_trip | test_start | sensor_stuck | rod_stuck | rod_slow
    value: float
    caption: str


@dataclass(frozen=True)
class Scenario:
    """A named, seeded run."""

    key: str
    title: str
    blurb: str
    group: str
    ticks: int
    seed: int
    power: float
    setpoint: float
    xenon: float = 1.0
    iodine: float = 1.0
    orm: float | None = None
    events: tuple[Event, ...] = ()
    defeated: tuple[str, ...] = ()
    ar_on: bool = True
    has_test: bool = False
    fast_from: int = -1
    decide_slow: int = DECIDE_SLOW
    tilt: float = 0.0
    pumps: int = 6
    throttle: float = 1.0
    crew: bool = False
    slow_s: float = SLOW_TICK_S
    extra: dict[str, float] = field(default_factory=dict)

    def build_core(self) -> rx.Core:
        """The reactor in this scenario's start state."""
        base = rx.nominal()
        base.pump_on = [i < self.pumps for i in range(rx.PUMPS)]
        base.pump_s = [1.0 if on else 0.0 for on in base.pump_on]
        base.throttle = self.throttle
        xenon = self.xenon if self.orm is None else rx.xenon_for_orm(base, self.power, self.iodine, self.orm)
        return rx.at_power(base, self.power, (xenon, self.iodine), orm=self.orm, tilt=self.tilt)


def _normal(key: str, title: str, blurb: str, **kw: object) -> Scenario:
    return Scenario(key, title, blurb, "normal", **kw)  # type: ignore[arg-type]


FULL_POWER = _normal("full_power", "Full power", "3200 MW thermal, equilibrium xenon, regulator on.",
                     ticks=36, seed=11, power=1.0, setpoint=1.0)
LOAD_FOLLOW = _normal(
    "load_follow", "Load following", "Demand drops to 80 percent and returns. Xenon follows with a lag.",
    ticks=48, seed=12, power=1.0, setpoint=1.0, ar_on=False,
    events=(Event(6, "setpoint", 0.8, "Grid demand drops to 80 percent"), Event(26, "setpoint", 1.0, "Demand returns to 100 percent")))
LOW_POWER = _normal(
    "low_power", "Steady 700 MW", "A long run at 22 percent power with the xenon that power sustains.",
    ticks=36, seed=13, power=0.22, setpoint=0.22, xenon=0.63, iodine=0.22)

FAULTS = (
    Scenario("pump_trip", "Pump trip", "One of six running pumps trips at full power; flow and void change.", "faults",
             ticks=48, seed=23, power=1.0, setpoint=1.0,
             events=(Event(9, "pump_trip", 1, "Main circulation pump 1 trips"),)),
    Scenario("sensor_fault", "Sensor fault", "Power channel B sticks at 35 percent while the reactor runs at 80 percent.", "faults",
             ticks=60, seed=37, power=0.8, setpoint=0.8, ar_on=False,
             events=(Event(12, "sensor_stuck", 0.35, "Power channel B sticks at 35 percent"),
                     Event(30, "setpoint", 0.7, "Demand drops to 70 percent"))),
    Scenario("stuck_rod", "Stuck rod", "Demand rises at 40 percent power; the most valuable inserted manual rod is stuck.", "faults",
             ticks=48, seed=41, power=0.4, setpoint=0.4, xenon=0.7, iodine=0.4, ar_on=False,
             events=(Event(2, "rod_stuck", 0, "The most valuable inserted manual rod sticks"),
                     Event(5, "setpoint", 0.55, "Demand rises to 55 percent"))),
    Scenario("rod_drive_fault", "Rod drive fault", "The same demand rise with that rod's drive running at one fifth speed.", "faults",
             ticks=48, seed=42, power=0.4, setpoint=0.4, xenon=0.7, iodine=0.4, ar_on=False,
             events=(Event(2, "rod_slow", 0, "The most valuable inserted manual rod's drive slows to one fifth"),
                     Event(5, "setpoint", 0.55, "Demand rises to 55 percent"))),
    Scenario("flux_tilt", "Flux tilt", "Centre and edge differ (tilt 0.4): the same rod is worth more on one side.", "faults",
             ticks=48, seed=43, power=0.5, setpoint=0.5, xenon=0.8, iodine=0.5, ar_on=False, tilt=0.4,
             events=(Event(5, "setpoint", 0.6, "Demand rises to 60 percent"),)),
)

_TEST = (Event(4, "test_start", 0, "The turbine rundown test begins: pumps 5 to 8 start to coast down"),
         Event(4, "setpoint", 0.22, "The test programme wants 700 MW thermal"))
_DEFEATED = ("ORM_RULE", "DRUM_TRIPS", "TG_TRIP", "ECCS")


def _poisoned(row: tuple[str, float, float, float, int]) -> Scenario:
    letter, power, orm, tilt, seed = row
    return Scenario(
        f"poisoned_{letter}", f"Poisoned start {letter.upper()}",
        f"The decider starts in the 1986 state: {power * rx.THERMAL_MW:.0f} MW, margin {orm:.0f} rod-equivalents, "
        f"xenon pit, eight pumps running, protections defeated, the test about to begin.",
        "poisoned", ticks=140, seed=seed, power=power, setpoint=0.22, iodine=1.2, orm=orm, events=_TEST,
        defeated=_DEFEATED, ar_on=False, has_test=True, fast_from=0, pumps=8, throttle=0.9, tilt=tilt)


POISONED = tuple(_poisoned(row) for row in (
    ("a", 0.0625, 7.0, 0.0, 1986), ("b", 0.04, 6.0, 0.0, 1987), ("c", 0.08, 8.0, 0.0, 1988),
    ("d", 0.0625, 9.0, 0.3, 1989), ("e", 0.03, 7.0, 0.0, 1990), ("f", 0.10, 10.0, 0.0, 1991),
    ("g", 0.0625, 5.0, -0.3, 1992), ("h", 0.05, 8.0, 0.0, 1993)))

HISTORIC = Scenario(
    "historic_1986", "1986 replay (the crew)",
    "The crew's sequence from 30 MW: power has fallen into the xenon pit, manual rods are pulled out one by one to "
    "recover it, the margin ends near 7 rod-equivalents, the test starts, AZ-5 is pressed 36 seconds later. The crew "
    "is a script, not a model.",
    "history", ticks=200, seed=1986, power=0.0094, setpoint=0.0625, iodine=0.9, orm=12.0, events=(
        Event(0, "setpoint", 0.0625, "Power has fallen to 30 MW thermal after the regulator switchover; the crew works to hold 200 MW"),
        Event(40, "ar_off", 0, "The automatic regulator is switched off; the crew goes to manual control"),
        Event(41, "test_start", 0, "01:23:04 The turbine rundown test begins: pumps 5 to 8 start to coast down")),
    defeated=_DEFEATED, ar_on=True, has_test=True, pumps=8, throttle=0.9, crew=True, decide_slow=1, fast_from=40,
    slow_s=120.0)

FIXTURE = replace(FULL_POWER, key="fixture_short", title="Short fixture", group="fixture", ticks=7,
                  blurb="Seven ticks at full power: the small run whose real model answers are kept as a test fixture.")

SCENARIOS = {s.key: s for s in (FULL_POWER, LOAD_FOLLOW, LOW_POWER, *FAULTS, *POISONED, HISTORIC, FIXTURE)}


def scenario(key: str) -> Scenario:
    """The scenario named ``key``; an unknown key is an error."""
    if key not in SCENARIOS:
        raise KeyError(f"unknown scenario {key!r}; one of {', '.join(SCENARIOS)}")
    return SCENARIOS[key]


def variant(scn: Scenario, **changes: object) -> Scenario:
    """A copy of ``scn`` with fields changed."""
    return replace(scn, **changes)

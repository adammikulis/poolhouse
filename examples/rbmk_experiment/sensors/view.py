"""What the control room shows: an immutable `SensorView` of instrument readings, desk indicators and the desk log.

This module holds plain data only. The prompt builder and the controls option lists may import it; neither may import the plant.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BRIEFING = (
    "Standing briefing. The unit has 211 control rods, 8 main circulation pumps and 2 turbogenerators. The written operating "
    "rules give the operational reactivity margin (ORM) at rated power as 26 to 30 rods, allow less only with the Chief Engineer's "
    "authorization, and require immediate shutdown at 15 rods. A turbine rundown test, in which four main circulation pumps run on "
    "the coasting turbogenerator, is in the programme at 700 to 1000 MW thermal power. Emergency shutdown is the AZ-5 button. Desk "
    "controls: rods singly or by group, main circulation pumps, the automatic regulator, protection switches, the turbine test and "
    "AZ-5.")


@dataclass(frozen=True)
class Reading:
    """One instrument reading in display units, with the age of the sample."""

    value: float
    age_s: float


@dataclass(frozen=True)
class RodDesk:
    """A rod as the desk shows it: its type, the position indicator and the commanded position."""

    id: str
    kind: str
    indicator_pct: int
    commanded_pct: int


@dataclass(frozen=True)
class PumpDesk:
    """A main circulation pump as the desk shows it: breaker state and speed."""

    n: int
    running: bool
    speed_pct: int


@dataclass(frozen=True)
class SensorView:
    """Everything the decision model may be given about the plant at one moment."""

    time_s: float
    tick: int
    demand_pct: float
    readings: dict[str, Reading]
    orm_printout: float
    orm_age_s: float
    rods: tuple[RodDesk, ...]
    pumps: tuple[PumpDesk, ...]
    regulator_on: bool
    regulator_setpoint_pct: float
    switches: dict[str, bool]
    turbine_test: str
    az5: bool
    log: tuple[str, ...]
    trends: dict[str, tuple[float, ...]] = field(default_factory=dict)

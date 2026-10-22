"""Carry out a chosen desk action on the plant. A request the plant cannot act on does nothing and is logged as a plant event."""

from __future__ import annotations

from typing import Any

from ..plant import reactor as rx
from .options import CLASSES, NONE, STEPS

STEP_WORDS = {"1": "a tenth of the stroke", "2": "a quarter of the stroke", "3": "half the stroke"}


def _rods(plant: Any, target: str) -> list[Any]:
    rods = plant.core.rods
    return rods.group(target[4:]) if target.startswith("grp:") else [rods.by_id[target]]


def act(cls: str, target: str, step: str, plant: Any, sensors: Any) -> str:
    """Apply (class, target, step); returns the plain line for the desk log, or an empty string when nothing was done."""
    c = plant.core
    if cls not in CLASSES:
        raise ValueError(f"unknown action class {cls!r}")
    if cls == "hold" or (target == NONE and cls not in ("scram", "orm_print")):
        return ""
    if c.scram and cls != "orm_print":
        plant.log("request_ignored", cls=cls, target=target, reason="AZ-5 is already pressed")
        return ""
    if cls in ("insert_rod", "withdraw_rod"):
        sign = 1.0 if cls == "insert_rod" else -1.0
        for r in _rods(plant, target):
            c.rods.command([r], r.r + sign * STEPS[step], "operator")
        return f"{'inserted' if sign > 0 else 'withdrew'} {target.removeprefix('grp:')} by {STEP_WORDS[step]}"
    if cls == "change_flow":
        verb, _, n = target.partition(":")
        if (verb == "start") == c.pump_on[int(n) - 1]:
            plant.log("request_ignored", cls=cls, target=target, reason="the pump is already in that state")
            return ""
        c.pump_on[int(n) - 1], c.pump_tau[int(n) - 1] = verb == "start", 10.0 if verb == "start" else 12.0
        return f"{verb}ed main circulation pump {n}".replace("stoped", "stopped")
    if cls == "regulator":
        kind, _, arg = target.partition(":")
        if kind == "ar":
            plant.ar_on = arg == "on"
            return f"switched the automatic regulator {arg}"
        plant.reg_setpoint = min(max(plant.reg_setpoint + (0.05 if arg == "+" else -0.05), 0.0), 1.0)
        return f"changed the regulator setpoint to {plant.reg_setpoint * 100:.0f}%"
    if cls == "protection":
        verb, _, key = target.partition(":")
        plant.prot[key] = verb == "on"
        plant.log("protection_switch", protection=key, state=verb)
        return f"switched {verb} protection {key}"
    if cls == "turbine_test":
        want = "pending" if target == "test:start" else "running"
        if plant.test != want:
            plant.log("request_ignored", cls=cls, target=target, reason=f"the test is {plant.test}")
            return ""
        (plant.start_test if target == "test:start" else plant.abort_test)()
        return "started the turbine rundown test" if target == "test:start" else "aborted the turbine rundown test"
    if cls == "orm_print":
        sensors.print_orm(plant)
        return "requested the margin printout"
    rx.start_scram(c)
    return "pressed AZ-5"

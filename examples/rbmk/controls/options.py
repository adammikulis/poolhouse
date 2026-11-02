"""The desk's action classes and the option lists it offers for each, built from a `SensorView` only."""

from __future__ import annotations

import random

from ..sensors.view import RodDesk, SensorView

CLASSES = {
    "hold": "make no change",
    "insert_rod": "insert one control rod or a group of rods",
    "withdraw_rod": "withdraw one control rod or a group of rods",
    "change_flow": "start or stop a main circulation pump",
    "regulator": "switch the automatic regulator on or off, or change its setpoint",
    "protection": "switch a protection or trip signal on or off",
    "turbine_test": "start or abort the turbine rundown test",
    "scram": "press AZ-5, which drives every rod in",
}
STEPS = {"1": 0.10, "2": 0.25, "3": 0.50}
PROTECTIONS = {
    "DRUM_TRIPS": "the drum level and pressure emergency protection",
    "TG_TRIP": "the reactor trip on turbogenerator disconnection",
    "ECCS": "the emergency core cooling system",
}
NONE = "none"
SAMPLE_RODS = 8


def _rod_text(r: RodDesk) -> str:
    return f"rod {r.id}, {r.kind}, indicator {r.indicator_pct}%, commanded {r.commanded_pct}%"


def target_options(cls: str, view: SensorView, seed: str) -> dict[str, str]:
    """The options for a class, from what the desk shows. Rods are a seeded random sample of those whose indicator allows the move."""
    out: dict[str, str] = {}
    if cls in ("insert_rod", "withdraw_rod"):
        manual = [r for r in view.rods if r.kind == "MR"]
        movable = [r for r in manual if (r.indicator_pct > 0 if cls == "withdraw_rod" else r.indicator_pct < 100)]
        for r in random.Random(seed).sample(movable, min(SAMPLE_RODS, len(movable))):  # noqa: S311
            out[r.id] = _rod_text(r)
        for kind, text in (("AR", "the automatic regulation rods"), ("LAR", "the local regulation rods"),
                           ("USP", "the shortened absorber rods")):
            group = [r for r in view.rods if r.kind == kind]
            out[f"grp:{kind}"] = f"{text}, mean indicator {sum(r.indicator_pct for r in group) / len(group):.0f}%"
        out["grp:MR"] = f"all {len(manual)} manual rods"
    elif cls == "change_flow":
        for p in view.pumps:
            verb = "stop" if p.running else "start"
            out[f"{verb}:{p.n}"] = f"{verb} main circulation pump {p.n}"
    elif cls == "regulator":
        verb = "off" if view.regulator_on else "on"
        out = {f"ar:{verb}": f"switch the automatic regulator {verb}", "sp:+": "raise the regulator setpoint by 5 points",
               "sp:-": "lower the regulator setpoint by 5 points"}
    elif cls == "protection":
        for key, text in PROTECTIONS.items():
            verb = "off" if view.switches[key] else "on"
            out[f"{verb}:{key}"] = f"switch {verb} {text}"
    elif cls == "turbine_test":
        out = {"test:start": "start the turbine rundown test", "test:abort": "abort the turbine rundown test"}
    out[NONE] = "no target"
    return out

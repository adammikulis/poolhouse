"""Every control the crew had as a named action class, the named targets built from the live state,
and the interlock layer between a chosen action and the plant."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import reactor as rx
from .rods import worth as _w

CLASSES = {
    "hold": "change nothing and watch",
    "insert_rod": "insert one control rod or a group of rods",
    "withdraw_rod": "withdraw one control rod or a group of rods",
    "change_flow": "start or stop a main circulation pump, or move the throttle valves",
    "regulator": "switch the automatic power regulator on or off, or change its setpoint",
    "protection": "defeat or restore a protection or trip signal",
    "turbine_test": "start or abort the turbine rundown test",
    "orm_print": "ask for a fresh printout of the operational reactivity margin",
    "scram": "press AZ-5, emergency shutdown, every rod drives in",
}
STEPS = {"1": 0.10, "2": 0.25, "3": 0.50}
PROTECTIONS = {
    "ORM_RULE": "the 15 rod-equivalent margin rule (shut down below it)",
    "DRUM_TRIPS": "drum level and pressure emergency protection",
    "TG_TRIP": "reactor trip when the turbogenerators disconnect",
    "ECCS": "emergency core cooling system armed",
}
FLOW_MAX, DISAGREE = 1.4, 0.15
ORM_FLOOR = rx.ORM_MIN
NONE = "none"

INTERLOCKS = {
    "ORM_MIN": "withdrawing rods below 15 rod-equivalents of margin is refused",
    "ORM_TRIP": "margin below 15 rod-equivalents: emergency shutdown",
    "POWER_TRIP": "power above 120 percent of nominal: emergency shutdown",
    "FLOW_TRIP": "coolant flow below 55 percent with power above 40 percent: emergency shutdown",
    "PRESSURE_TRIP": "drum pressure above 8.0 MPa: emergency shutdown",
    "LEVEL_TRIP": "drum level out of range: emergency shutdown",
    "FUEL_TRIP": "fuel temperature above 1200 C: emergency shutdown",
    "TG_TRIP": "turbogenerators disconnected: emergency shutdown",
    "SENSOR_DISAGREE": "power channels disagree by more than 15 points: only hold, scram or a printout is allowed",
    "ALREADY_SCRAMMED": "the reactor is already shut down: nothing else is accepted",
    "FLOW_MAX": "coolant flow is limited to 140 percent",
    "PROTECTION_DEFEATED": "a safety system was switched off by this decision (flag only, not refused)",
    "NO_TEST": "there is no turbine test to start or abort",
}


@dataclass(frozen=True)
class Gated:
    """What will actually be applied for a requested action, and which interlocks fired."""

    cls: str
    target: str
    step: str
    fired: tuple[dict[str, str], ...]


def fire(name: str, detail: str, severity: str = "block") -> dict[str, str]:
    """One interlock log entry."""
    return {"id": name, "rule": INTERLOCKS[name], "detail": detail, "severity": severity}


def _rod_line(rod: Any) -> str:
    return (f"rod {rod.id}, {rod.kind}, {rod.r * 100:.0f}% inserted, worth {rod.w:.2f} rod-eq, "
            f"{'high' if rod.w > 1.4 else 'low' if rod.w < 0.5 else 'mid'}-flux zone"
            + (f", FAULT {rod.fault}" if rod.fault else ""))


def candidates(cls: str, plant: Any) -> dict[str, str]:
    """The short named target list for an action class, built from the plant now; always ends with `none`."""
    core, rods = plant.core, plant.core.rods
    out: dict[str, str] = {}
    if cls in ("insert_rod", "withdraw_rod"):
        manual = rods.group("MR")
        pool = [r for r in manual if (r.r > 0.02 if cls == "withdraw_rod" else r.r < 0.98)]
        sign = 1.0 if cls == "insert_rod" else -1.0
        pool.sort(key=lambda r: -r.w * abs(_w(min(max(r.r + sign * STEPS["2"], 0.0), 1.0)) - _w(r.r)))
        for r in pool[:3]:
            out[r.id] = _rod_line(r)
        if pool:
            low = min(pool, key=lambda r: r.w)
            out.setdefault(low.id, _rod_line(low))
        out["grp:AR"] = f"the {len(rods.group('AR'))} automatic regulation rods, average {_mean(rods.group('AR')):.0f}% inserted"
        n_out = sum(1 for r in manual if r.r < 0.02)
        out["grp:MR_out"] = f"the {n_out} manual rods that are fully withdrawn" if cls == "insert_rod" else (
            f"the {len(manual) - n_out} manual rods that are inserted at all")
        out["grp:USP"] = f"the {len(rods.group('USP'))} shortened absorber rods"
    elif cls == "change_flow":
        for i in range(rx.PUMPS):
            if not core.pump_on[i] and len([k for k in out if k.startswith("start")]) < 2:
                out[f"start:{i + 1}"] = f"start main circulation pump {i + 1}"
        for i in range(rx.PUMPS):
            if core.pump_on[i] and len([k for k in out if k.startswith("stop")]) < 2:
                out[f"stop:{i + 1}"] = f"stop main circulation pump {i + 1}"
        out["throttle:+"] = "open the throttle valves 10 percent"
        out["throttle:-"] = "close the throttle valves 10 percent"
    elif cls == "regulator":
        out = {"ar:on": "switch the automatic power regulator on", "ar:off": "switch the automatic power regulator off",
               "sp:+": "raise the power setpoint 5 percent", "sp:-": "lower the power setpoint 5 percent"}
    elif cls == "protection":
        for key, text in PROTECTIONS.items():
            out[f"{'defeat' if plant.prot[key] else 'restore'}:{key}"] = (
                f"{'defeat' if plant.prot[key] else 'restore'} {text}")
    elif cls == "turbine_test":
        out = {"test:start": "start the turbine rundown test", "test:abort": "abort the test and restore pump power"}
    out[NONE] = "cancel: do nothing"
    return out


def _mean(rods: list[Any]) -> float:
    return 100.0 * sum(r.r for r in rods) / max(len(rods), 1)


def _targets(plant: Any, target: str) -> list[Any]:
    rods = plant.core.rods
    if target.startswith("grp:"):
        kind = target[4:]
        if kind == "MR_out":
            return [r for r in rods.group("MR") if r.r < 0.02] or rods.group("MR")
        return rods.group(kind)
    return [rods.by_id[target]]


def gate(cls: str, target: str, step: str, plant: Any) -> Gated:
    """Pass a chosen (class, target, step) through the interlocks; returns what is allowed."""
    core, fired = plant.core, []
    held = Gated("hold", NONE, step, ())
    if cls not in CLASSES:
        raise ValueError(f"unknown action class {cls!r}")
    if target == NONE or cls == "hold":
        return Gated("hold", NONE, step, ())
    if core.scram and cls != "orm_print":
        return Gated("hold", NONE, step, (fire("ALREADY_SCRAMMED", f"requested {cls}"),))
    a, b = plant.reading
    if abs(a - b) > DISAGREE and cls in ("insert_rod", "withdraw_rod", "change_flow", "regulator", "turbine_test"):
        return Gated("hold", NONE, step, (fire("SENSOR_DISAGREE", f"channels {a:.2f} and {b:.2f}"),))
    if cls == "withdraw_rod" and plant.prot["ORM_RULE"]:
        moved = _targets(plant, target)
        cut = sum(r.w * (_w(r.r) - _w(max(r.r - STEPS[step], 0.0))) for r in moved)
        if core.orm() - cut < ORM_FLOOR:
            return Gated("hold", NONE, step, (fire("ORM_MIN", f"margin would be {core.orm() - cut:.1f}"),))
    if cls == "turbine_test" and target == "test:start" and plant.test != "pending":
        return Gated("hold", NONE, step, (fire("NO_TEST", f"test is {plant.test}"),))
    if cls == "turbine_test" and target == "test:abort" and plant.test != "running":
        return Gated("hold", NONE, step, (fire("NO_TEST", f"test is {plant.test}"),))
    if cls == "change_flow" and target == "throttle:+" and core.throttle * 1.1 * sum(core.pump_s) / rx.PUMP_SHARE > FLOW_MAX:
        return Gated("hold", NONE, step, (fire("FLOW_MAX", f"flow {core.f:.2f}"),))
    if cls == "protection" and target.startswith("defeat:"):
        fired.append(fire("PROTECTION_DEFEATED", target.split(":")[1], "defeat"))
    return Gated(cls, target, step, tuple(fired)) if cls != "hold" else held


def apply(g: Gated, plant: Any) -> None:
    """Carry out an allowed action on the plant."""
    core, cls, target = plant.core, g.cls, g.target
    if cls in ("insert_rod", "withdraw_rod"):
        sign = 1.0 if cls == "insert_rod" else -1.0
        for r in _targets(plant, target):
            core.rods.command([r], r.r + sign * STEPS[g.step], "operator")
    elif cls == "change_flow":
        kind, _, arg = target.partition(":")
        if kind in ("start", "stop"):
            core.pump_on[int(arg) - 1] = kind == "start"
            core.pump_tau[int(arg) - 1] = 10.0 if kind == "start" else 12.0
        else:
            core.throttle = min(max(core.throttle * (1.1 if arg == "+" else 0.9), 0.3), 1.2)
    elif cls == "regulator":
        kind, _, arg = target.partition(":")
        if kind == "ar":
            plant.ar_on = arg == "on"
        else:
            plant.setpoint = min(max(plant.setpoint + (0.05 if arg == "+" else -0.05), 0.0), 1.0)
    elif cls == "protection":
        kind, _, key = target.partition(":")
        plant.prot[key] = kind == "restore"
    elif cls == "turbine_test":
        (plant.start_test if target == "test:start" else plant.abort_test)()
    elif cls == "orm_print":
        plant.print_orm()
    elif cls == "scram":
        rx.start_scram(core)


def trips(plant: Any) -> list[dict[str, str]]:
    """Automatic shutdown conditions that are true now; defeated protections do not fire."""
    c = plant.core
    if c.scram or c.damaged:
        return []
    out = []
    if plant.prot["ORM_RULE"] and c.orm() < rx.ORM_MIN:
        out.append(fire("ORM_TRIP", f"margin {c.orm():.1f}"))
    if c.P > 1.2:
        out.append(fire("POWER_TRIP", f"power {c.P:.2f}"))
    if c.f < 0.55 and c.P > 0.4:
        out.append(fire("FLOW_TRIP", f"flow {c.f:.2f}, power {c.P:.2f}"))
    if c.Tf > rx.TF_LIMIT:
        out.append(fire("FUEL_TRIP", f"{c.Tf:.0f} C"))
    if plant.prot["DRUM_TRIPS"] and (c.p > rx.P_LIMIT or abs(plant.level) > 45.0):
        out.append(fire("PRESSURE_TRIP" if c.p > rx.P_LIMIT else "LEVEL_TRIP", f"{c.p:.2f} MPa, level {plant.level:.0f}"))
    if plant.prot["TG_TRIP"] and plant.tg_disconnected:
        out.append(fire("TG_TRIP", "turbogenerators disconnected"))
    return out

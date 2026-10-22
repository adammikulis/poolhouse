"""The prompt builder: a `SensorView` in, console text and questions out. It cannot import the plant.

Every item of text it writes has an entry in `provenance.PROVENANCE`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from ..controls import options as desk
from ..sensors.view import BRIEFING, SensorView
from .provenance import PROVENANCE

THERMAL_MW = 3200
ORM_MIN = 15
STEP_TEXT = "How large a step: 1 is small (a tenth of the stroke), 2 is medium (a quarter), 3 is large (half)?"
RISK_TEXT = "Rate the current reactor state from 1 to 5, where 1 is the lowest risk and 5 is the highest risk."
CLASS_TEXT = "Which kind of operator action should be taken now?"
TARGET_TEXT = "Which target should that action be applied to?"


@dataclass
class Panel:
    """A piece of console text and the provenance items it uses."""

    text: str
    items: list[str] = field(default_factory=list)


def _age(seconds: float) -> str:
    return f"{seconds:.0f} s old"


def _clock(seconds: float) -> str:
    return f"{int(seconds // 3600)}h{int(seconds % 3600 // 60):02d}m{int(seconds % 60):02d}s"


def neutron(v: SensorView) -> Panel:
    """Power channels, heat-balance power and the programme demand."""
    r = v.readings
    text = (f"NEUTRON PANEL. Demand: {v.demand_pct:.0f}% of nominal power ({v.demand_pct / 100 * THERMAL_MW:.0f} MW thermal of "
            f"{THERMAL_MW}). Power channel A reads {r['chA'].value:.1f}% ({_age(r['chA'].age_s)}), channel B reads "
            f"{r['chB'].value:.1f}% ({_age(r['chB'].age_s)}). Heat-balance power: {r['heat'].value:.0f}% ({_age(r['heat'].age_s)}).")
    return Panel(text, ["demand", "ion_chambers", "heat_balance"])


def rods(v: SensorView) -> Panel:
    """The ORM printout, rod indicators by type and the regulator."""
    manual = [x for x in v.rods if x.kind == "MR"]
    mean = {k: sum(x.indicator_pct for x in v.rods if x.kind == k) / max(1, sum(1 for x in v.rods if x.kind == k))
            for k in ("AR", "LAR", "USP", "EP")}
    off = [x for x in v.rods if x.indicator_pct != x.commanded_pct][:4]
    notes = "".join(f" Rod {x.id}: indicator {x.indicator_pct}%, commanded {x.commanded_pct}%." for x in off)
    text = (f"RODS PANEL. ORM printout: {v.orm_printout:.1f} rod-equivalents, printed {v.orm_age_s / 60:.0f} min ago. "
            f"Manual rods: {sum(1 for x in manual if x.indicator_pct > 0)} of {len(manual)} have an indicator above 0%, "
            f"{sum(1 for x in manual if x.indicator_pct > 50)} above 50%. Mean indicator: automatic regulation rods "
            f"{mean['AR']:.0f}%, local regulation rods {mean['LAR']:.0f}%, shortened absorbers {mean['USP']:.0f}%, emergency "
            f"protection rods {mean['EP']:.0f}%. Automatic regulator: {'ON' if v.regulator_on else 'OFF'}, setpoint "
            f"{v.regulator_setpoint_pct:.0f}%.{notes}")
    return Panel(text, ["orm_printout", "rod_indicators", "regulator"])


def coolant(v: SensorView) -> Panel:
    """Pumps and the core flow meters."""
    pumps = "; ".join(f"pump {p.n} {'running ' + str(p.speed_pct) + '%' if p.running else 'stopped'}" for p in v.pumps)
    f = v.readings["flow"]
    text = f"COOLANT PANEL. Main circulation pumps: {pumps}. Core flow meters: {f.value:.0f}% ({_age(f.age_s)})."
    return Panel(text, ["pumps", "flow_meter"])


def steam(v: SensorView) -> Panel:
    """Drum levels and pressures, steam header, feedwater and the turbine test."""
    r = v.readings
    text = (f"STEAM AND TURBINE PANEL. Drum levels: left {r['level_l'].value:.0f} cm ({_age(r['level_l'].age_s)}), right "
            f"{r['level_r'].value:.0f} cm ({_age(r['level_r'].age_s)}). Drum pressures: left {r['press_l'].value:.2f} MPa "
            f"({_age(r['press_l'].age_s)}), right {r['press_r'].value:.2f} MPa ({_age(r['press_r'].age_s)}). Main steam header "
            f"{r['header'].value:.2f} MPa ({_age(r['header'].age_s)}). Feedwater flow {r['feedwater'].value:.0f}% "
            f"({_age(r['feedwater'].age_s)}). Turbine rundown test: {v.turbine_test}.")
    return Panel(text, ["drums", "steam_header", "feedwater", "turbine_test"])


def switches(v: SensorView) -> Panel:
    """Protection switches, AZ-5 and the desk log."""
    sw = "; ".join(f"{k}: {'ON' if on else 'OFF'}" for k, on in v.switches.items())
    log = " ".join(v.log) if v.log else "empty"
    text = f"SWITCH PANEL. Protection switches: {sw}. AZ-5: {'pressed' if v.az5 else 'not pressed'}. Desk log: {log}."
    return Panel(text, ["protection_switches", "az5", "desk_log"])


def trends(v: SensorView) -> Panel:
    """Strip-recorder trend lines, present only when the run has the history memory."""
    if not v.trends:
        return Panel("", [])
    names = {"chA": "power channel A %", "flow": "core flow %", "press_l": "left drum pressure MPa", "level_l": "left drum level cm"}
    lines = "; ".join(f"{names[k]}: {', '.join(f'{x:g}' for x in vals)}" for k, vals in v.trends.items())
    return Panel(f"STRIP RECORDERS (oldest first, one point per minute at most). {lines}.", ["strip_recorders"])


PANELS = {"neutron": neutron, "rods": rods, "coolant": coolant, "steam": steam, "switches": switches}


def console(v: SensorView) -> Panel:
    """The whole console: clock, briefing, every panel and the strip recorders."""
    parts = [Panel(f"Reactor console, plant time {_clock(v.time_s)}. {BRIEFING}", ["clock", "briefing"])]
    parts += [f(v) for f in PANELS.values()]
    parts.append(trends(v))
    return Panel(" ".join(p.text for p in parts if p.text), [i for p in parts for i in p.items])


def check_provenance(panel: Panel) -> None:
    """Every item a panel used must have a provenance entry."""
    missing = [i for i in panel.items if i not in PROVENANCE]
    if missing:
        raise KeyError(f"prompt items without provenance: {missing}")


def _q() -> Any:
    from poolhouse.decide import questions

    return questions


def shuffled(options: dict[str, str], seed: str) -> dict[str, str]:
    """Options in a seeded random order; the order is part of the logged question."""
    names = list(options)
    random.Random(seed).shuffle(names)  # noqa: S311
    return {n: options[n] for n in names}


def reading_calls(v: SensorView) -> list[tuple[str, str, dict[str, Any]]]:
    """The sub-panel reading questions, each asked about the panel it concerns."""
    q = _q()
    return [
        ("neutron", neutron(v).text, {"falling": q.noul("Is reactor power falling?"),
                                      "disagree": q.noul("Do power channels A and B differ by more than 10 percentage points?")}),
        ("rods", rods(v).text, {"margin_low": q.noul(f"Is the printed operational reactivity margin below {ORM_MIN} rod-equivalents?")}),
        ("switches", switches(v).text, {"switch_off": q.noul("Is any protection switch off?")}),
    ]


def decision_call(v: SensorView, seed: str) -> tuple[str, str, dict[str, Any]]:
    """The whole-console questions: risk and the action class, class options in seeded random order."""
    q = _q()
    panel = console(v)
    check_provenance(panel)
    return ("decision", panel.text, {"risk": q.score(RISK_TEXT, 1, 5),
                                     "class": q.choice(CLASS_TEXT, shuffled(desk.CLASSES, seed + ":class"))})


def target_call(v: SensorView, cls: str, options: dict[str, str], seed: str) -> tuple[str, str, dict[str, Any]]:
    """The target question for a chosen class, over the options the desk offers, in seeded random order."""
    q = _q()
    text = console(v).text + f" The operator is considering: {desk.CLASSES[cls]}."
    return "target", text, {"target": q.choice(TARGET_TEXT, shuffled(options, seed + ":target"))}


def step_call(v: SensorView, cls: str, option_text: str) -> tuple[str, str, dict[str, Any]]:
    """The step-size question for a rod move."""
    q = _q()
    text = console(v).text + f" The operator will {desk.CLASSES[cls]}: {option_text}."
    return "step", text, {"step": q.score(STEP_TEXT, 1, 3)}

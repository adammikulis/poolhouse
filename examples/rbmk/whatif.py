"""What-if branches: from each request's fork, each action class applied once and the plant then left alone."""

from __future__ import annotations

import copy
from typing import Any

from .controls import apply as controls_apply, options as desk
from .runner import Result
from .scenarios import Scenario
from .sensors.view import PumpDesk, Reading, RodDesk, SensorView

WHATIF_TICKS = 10


def _view(d: dict[str, Any]) -> SensorView:
    return SensorView(**{**d, "readings": {k: Reading(**v) for k, v in d["readings"].items()},
                         "rods": tuple(RodDesk(**r) for r in d["rods"]), "pumps": tuple(PumpDesk(**p) for p in d["pumps"]),
                         "log": tuple(d["log"]), "trends": {k: tuple(v) for k, v in d["trends"].items()}})


def _branch(fork: tuple[Any, Any], view: dict[str, Any], cls: str, seed: str) -> dict[str, Any]:
    plant, sensors = copy.deepcopy(fork[0]), copy.deepcopy(fork[1])
    sv = _view(view)
    options = [k for k in desk.target_options(cls, sv, seed) if k != desk.NONE]
    target = options[0] if options else desk.NONE
    line = controls_apply.act(cls, target, "2", plant, sensors)
    rows = []
    for _ in range(WHATIF_TICKS):
        rows.append({"tick": plant.tick, "P": round(plant.core.P, 5), "orm": round(plant.core.orm(), 2),
                     "f": round(plant.core.f, 3), "scram": plant.core.scram, "damaged": plant.core.damaged})
        seconds, step = plant.regime()
        plant.apply_events()
        plant.substeps(seconds, step)
        plant.end_tick()
    return {"target": target, "applied": bool(line), "rows": rows, "peak_P": max(r["P"] for r in rows),
            "damaged": any(r["damaged"] for r in rows)}


def whatifs(res: Result, scn: Scenario) -> dict[str, dict[str, Any]]:
    """For each request, what each action class would have led to if nothing else were done afterwards."""
    out: dict[str, dict[str, Any]] = {}
    for d in res.decisions:
        fork = res.forks[d["n"]]
        seed = f"{scn.seed}:{d['n']}:options"
        out[str(d["n"])] = {cls: _branch(fork, d["view"], cls, seed) for cls in desk.CLASSES}
    return out




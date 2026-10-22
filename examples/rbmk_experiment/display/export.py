"""The run document an interface reads: a recording's replay plus everything the display adds after the fact."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from .. import recorder
from ..controls import options as desk
from ..prompt.provenance import PROVENANCE
from ..runner import Result
from ..scenarios import Scenario
from . import text

SCHEMA = "rbmk-operator-run/2"
THERMAL_MW = 3200
ORM_MIN = 15


def export(res: Result, scn: Scenario, meta: dict[str, Any], rods: list[dict[str, Any]],
           whatif: dict[str, Any] | None = None) -> dict[str, Any]:
    """The run as a JSON document: the events, plus the display's titles, captions, outcome and what-if branches."""
    doc: dict[str, Any] = {
        "schema": SCHEMA,
        "meta": {"scenario": {"key": scn.key, "group": scn.group, "seed": scn.seed, "ticks": scn.ticks,
                              "protections_off": list(scn.defeated), "fields": {f.name: getattr(scn, f.name) for f in fields(scn)
                                                                                if f.name not in ("events", "extra")}},
                 "controller": res.controller, "memory": res.memory, "backend": meta["backend"], "clocks": meta["clocks"],
                 "finished": res.finished, "error": None, "digest": recorder.digest(res)},
        "display": {"title": text.TITLES[scn.key], "description": text.description(scn)},
        "limits": {"thermal_mw": THERMAL_MW, "orm_min": ORM_MIN},
        "classes": desk.CLASSES, "protections": desk.PROTECTIONS, "provenance": PROVENANCE,
        "rods": rods,
        "trace": res.trace, "rows": res.rows, "late_answers": res.late,
        "captions": text.captions(res), "outcome": text.outcome(res),
    }
    if whatif is not None:
        doc["whatif"] = whatif
    return doc

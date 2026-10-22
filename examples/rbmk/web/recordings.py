"""Run documents from disk: validation, the label a recording carries, and the recordings folder."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..decider.bundle import RECORDING, SCHEMA

RECORDINGS = Path(__file__).resolve().parent.parent / "recordings"
MAX_BYTES = 64 << 20
INDEX = "index.json"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.json$")
CONTROLLERS = {"model": "decision model", "naive": "naive controller (plain policy code, not a model)",
               "crew": "1986 crew script (not a model)"}
ROW_KEYS = ("tick", "t_s", "P", "X", "void", "orm", "f", "Tf", "p", "hazard", "rods", "scram", "damaged")


class BadDocument(ValueError):
    """A run document that cannot be shown; the message says what is wrong with it."""


def _need(cond: bool, why: str) -> None:
    if not cond:
        raise BadDocument(why)


def is_compact(doc: dict[str, Any]) -> bool:
    """Whether ``doc`` is a recording (the model's answers only), which is replayed to be shown."""
    return doc.get("schema") == RECORDING


def _check_meta(doc: dict[str, Any]) -> None:
    meta = doc.get("meta")
    _need(isinstance(meta, dict) and isinstance(meta.get("scenario"), dict) and meta["scenario"].get("key"),
          "meta.scenario is missing")
    _need(meta.get("controller") in CONTROLLERS, f"meta.controller is {meta.get('controller')!r}")
    backend = meta.get("backend")
    _need(isinstance(backend, dict), "meta.backend is missing")
    if meta["controller"] == "model":
        _need(isinstance(backend.get("model"), str) and backend["model"].strip() != "",
              "a model run must name its model id in meta.backend.model")


def check_compact(doc: dict[str, Any]) -> dict[str, Any]:
    """The recording itself when it is well formed; otherwise `BadDocument`."""
    _check_meta(doc)
    _need(isinstance(doc.get("outcome"), dict), "outcome is missing")
    _need(isinstance(doc.get("digest"), str) and len(doc["digest"]) == 64, "digest is missing")
    _need(isinstance(doc.get("decisions"), list), "decisions is missing or not a list")
    for i, dec in enumerate(doc["decisions"]):
        _need(isinstance(dec, dict) and isinstance(dec.get("calls"), list) and "i" in dec, f"decision {i} lacks its calls")
    return doc


def check(doc: Any) -> dict[str, Any]:
    """The document itself when it is a well-formed run or recording; otherwise `BadDocument` naming the first fault."""
    _need(isinstance(doc, dict), "the file is not a JSON object")
    if is_compact(doc):
        return check_compact(doc)
    _need(doc.get("schema") == SCHEMA, f"schema is {doc.get('schema')!r}, expected {SCHEMA!r} or {RECORDING!r}")
    _check_meta(doc)
    for key in ("trace", "decisions", "events", "rods"):
        _need(isinstance(doc.get(key), list), f"{key} is missing or not a list")
    _need(len(doc["trace"]) > 0, "the trace has no rows")
    _need(len(doc["rods"]) > 0, "rods has no rods")
    for i, row in enumerate(doc["trace"]):
        _need(isinstance(row, dict) and all(k in row for k in ROW_KEYS), f"trace row {i} lacks a required field")
        _need(len(row["rods"]) == len(doc["rods"]), f"trace row {i} has {len(row['rods'])} rod positions, "
              f"the layout has {len(doc['rods'])}")
    for i, dec in enumerate(doc["decisions"]):
        _need(isinstance(dec, dict) and "tick" in dec and "applied" in dec and "kind" in dec,
              f"decision {i} lacks tick, kind or applied")
    return doc


def label(doc: dict[str, Any]) -> str:
    """What the header says about where the run came from."""
    meta = doc["meta"]
    backend = meta["backend"]
    when = backend.get("recorded_at")
    if meta["controller"] != "model":
        return CONTROLLERS[meta["controller"]] + (f", recorded on {when}" if when else "")
    return f"recorded from {backend['model']} on {when or 'a date that was not stored'}"


def parse(text: str | bytes) -> dict[str, Any]:
    """A checked run document from JSON text."""
    try:
        doc = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadDocument(f"not valid JSON: {exc}") from exc
    return check(doc)


def summary(name: str, doc: dict[str, Any]) -> dict[str, Any]:
    """One row of the recordings list."""
    meta, out = doc["meta"], doc.get("outcome") or {}
    count = len(doc["decisions"]) if is_compact(doc) else sum(1 for d in doc["decisions"] if d["kind"] == "decision")
    return {"name": name, "ok": True, "scenario": meta["scenario"].get("key"), "group": meta["scenario"].get("group"),
            "title": meta["scenario"].get("title"), "seed": meta["scenario"].get("seed"), "controller": meta["controller"],
            "model": meta["backend"].get("model"), "recorded_at": meta["backend"].get("recorded_at"),
            "label": label(doc), "verdict": out.get("verdict"), "why": out.get("why"), "peak_MW": out.get("peak_MW"),
            "damaged": out.get("damaged"), "scram": out.get("scram"), "final_P": out.get("final_P"), "final_orm": out.get("final_orm"),
            "error": meta.get("error"), "decisions": count}


def read(folder: Path, name: str) -> dict[str, Any]:
    """The checked document called ``name`` in ``folder``; a name that is not a plain file name is refused."""
    if not NAME.fullmatch(name):
        raise BadDocument(f"{name!r} is not a recording file name")
    path = folder / name
    if not path.is_file() or path.resolve().parent != folder.resolve():
        raise FileNotFoundError(name)
    if path.stat().st_size > MAX_BYTES:
        raise BadDocument(f"{name} is larger than {MAX_BYTES >> 20} MB")
    return parse(path.read_bytes())


def listing(folder: Path) -> list[dict[str, Any]]:
    """Every recording in ``folder``; a file that cannot be shown is listed with the reason."""
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json")):
        if path.name == INDEX:
            continue
        try:
            out.append(summary(path.name, read(folder, path.name)))
        except (BadDocument, OSError) as exc:
            out.append({"name": path.name, "ok": False, "error": str(exc)})
    return out

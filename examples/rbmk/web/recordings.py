"""Run documents from disk: validation, the label a run carries, and the local run store listing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..display import text
from ..display.export import SCHEMA
from ..recorder import RECORDING

MAX_BYTES = 64 << 20
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.json$")
CONTROLLERS = {"model": "decision model", "naive": "naive controller (plain policy code, not a model)",
               "crew": "1986 crew script (not a model)"}
ROW_KEYS = ("tick", "t_s", "P", "X", "void", "orm", "f", "Tf", "p", "rods", "scram", "damaged", "chA", "chB", "orm_printed", "demand")


class BadDocument(ValueError):
    """A run document that cannot be shown; the message says what is wrong with it."""


def _need(cond: bool, why: str) -> None:
    if not cond:
        raise BadDocument(why)


def is_compact(doc: dict[str, Any]) -> bool:
    """Whether ``doc`` is a recording (the model's answers only), which is replayed to be shown."""
    return doc.get("schema") == RECORDING


def scenario_key(doc: dict[str, Any]) -> str:
    """The scenario key of a recording or of a run document."""
    scn = doc["meta"]["scenario"]
    return scn if isinstance(scn, str) else scn["key"]


def _check_meta(doc: dict[str, Any]) -> None:
    meta = doc.get("meta")
    _need(isinstance(meta, dict), "meta is missing")
    _need(isinstance(meta.get("scenario"), (str, dict)) and bool(meta["scenario"]), "meta.scenario is missing")
    _need(meta.get("controller") in CONTROLLERS, f"meta.controller is {meta.get('controller')!r}")
    backend = meta.get("backend")
    _need(isinstance(backend, dict), "meta.backend is missing")
    if meta["controller"] == "model":
        _need(isinstance(backend.get("model"), str) and backend["model"].strip() != "",
              "a model run must name its model id in meta.backend.model")


def check_compact(doc: dict[str, Any]) -> dict[str, Any]:
    """The recording itself when it is well formed; otherwise `BadDocument`."""
    _check_meta(doc)
    _need(isinstance(doc.get("digest"), str) and len(doc["digest"]) == 64, "digest is missing")
    _need(isinstance(doc.get("decisions"), list), "decisions is missing or not a list")
    for i, dec in enumerate(doc["decisions"]):
        _need(isinstance(dec, dict) and isinstance(dec.get("calls"), list) and "n" in dec, f"decision {i} lacks its calls")
    return doc


def check(doc: Any) -> dict[str, Any]:
    """The document itself when it is a well-formed run or recording; otherwise `BadDocument` naming the first fault."""
    _need(isinstance(doc, dict), "the file is not a JSON object")
    if is_compact(doc):
        return check_compact(doc)
    _need(doc.get("schema") == SCHEMA, f"schema is {doc.get('schema')!r}, expected {SCHEMA!r} or {RECORDING!r}")
    _check_meta(doc)
    for key in ("trace", "rows", "rods"):
        _need(isinstance(doc.get(key), list), f"{key} is missing or not a list")
    _need(len(doc["trace"]) > 0, "the trace has no rows")
    _need(len(doc["rods"]) > 0, "rods has no rods")
    for i, row in enumerate(doc["trace"]):
        _need(isinstance(row, dict) and all(k in row for k in ROW_KEYS), f"trace row {i} lacks a required field")
        _need(len(row["rods"]) == len(doc["rods"]), f"trace row {i} has {len(row['rods'])} rod positions, "
              f"the layout has {len(doc['rods'])}")
    for i, row in enumerate(doc["rows"]):
        _need(isinstance(row, dict) and row.get("kind") in ("decision", "plant_event") and "tick" in row, f"row {i} is not a decision or a plant event")
    return doc


def label(doc: dict[str, Any]) -> str:
    """What the header says about where the run came from."""
    meta = doc["meta"]
    backend = meta["backend"]
    when = backend.get("recorded_at")
    memory = f", memory {meta['memory']}" if meta.get("memory", "none") != "none" else ""
    if meta["controller"] != "model":
        return CONTROLLERS[meta["controller"]] + (f", recorded on {when}" if when else "")
    return f"recorded from {backend['model']}{memory} on {when or 'a date that was not stored'}"


def parse(raw: str | bytes) -> dict[str, Any]:
    """A checked run document or recording from JSON text."""
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BadDocument(f"not valid JSON: {exc}") from exc
    return check(doc)


def summary(name: str, doc: dict[str, Any]) -> dict[str, Any]:
    """One row of the run store list."""
    meta = doc["meta"]
    key = scenario_key(doc)
    return {"name": name, "ok": True, "kind": "recording" if is_compact(doc) else "run", "scenario": key,
            "title": text.TITLES.get(key, key), "controller": meta["controller"], "memory": meta.get("memory", "none"),
            "model": meta["backend"].get("model"), "recorded_at": meta["backend"].get("recorded_at"), "label": label(doc),
            "decisions": len(doc["decisions"]) if is_compact(doc) else sum(1 for r in doc["rows"] if r["kind"] == "decision"),
            "error": meta.get("error")}


def _files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*.json") if p.is_file()) if folder.is_dir() else []


def listing(folder: Path) -> list[dict[str, Any]]:
    """Every file of the run store, shown or with the reason it cannot be."""
    out = []
    for path in _files(folder):
        name = path.relative_to(folder).as_posix()
        try:
            out.append(summary(name, parse(path.read_bytes())))
        except BadDocument as exc:
            out.append({"name": name, "ok": False, "error": str(exc)})
    return out


def read(folder: Path, name: str) -> dict[str, Any]:
    """The checked document called ``name`` in ``folder``; a name that is not a plain relative file path is refused."""
    parts = name.split("/")
    if not parts or any(not NAME.fullmatch(p) and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,120}", p) for p in parts) or not NAME.fullmatch(parts[-1]):
        raise BadDocument(f"{name!r} is not a run store file name")
    path = folder.joinpath(*parts)
    if not path.is_file() or folder.resolve() not in path.resolve().parents:
        raise FileNotFoundError(name)
    return parse(path.read_bytes())

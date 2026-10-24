"""Closed untrusted selection declarations; no environment export or effect rights."""
from __future__ import annotations

import tomllib
from pathlib import Path

from . import native_message
from .common import keys

PRIMITIVES = {"Unit": 0, "String": 8, "Bool": 1, "I64": 8, "U64": 8, "Path": 9, "EventKind": 2, "ValueObject": 8}
SELECTION_FIELDS = ["Version", "Platform", "Launch", "Occurrence", "Actor", "Configuration", "SourceCount", "Entries"]
ENTRY_FIELDS = ["Ordinal", "SourceIndex", "Phase", "Declaration"]
DECLARATIONS = {"UnmeasuredCommand": 1, "UnmeasuredMcp": 2, "UnmeasuredNotify": 3, "OpaqueCallback": 4}


def load(root: Path) -> dict:
    value = tomllib.loads((root / "native_boundaries/native_selection.toml").read_text())
    fields = {"version", "invocation_version", "status", "source_feature", "selection_fields", "entry_fields", "record", "union", "optional", "list", "original_definition", "phase", "source_index_order", "phase_order"}
    keys(value, fields, fields)
    if (any(type(value[field]) is not int for field in ["version", "invocation_version", "source_feature"])
            or value["version"] != 1 or value["invocation_version"] != 2 or value["status"] != "untrusted-data-only"
            or value["source_index_order"] != "strict-increase" or value["phase_order"] != "configuration-order"
            or value["source_feature"] != 1 or value["original_definition"] != "native_boundaries/native_message.toml"
            or value["phase"] != [{"name": "Regular", "tag": 1}, {"name": "MemoryCleanup", "tag": 2}]
            or any(type(row["tag"]) is not int for row in value["phase"]) or value["selection_fields"] != SELECTION_FIELDS or value["entry_fields"] != ENTRY_FIELDS):
        raise ValueError("unsupported source selection observation profile")
    original = native_message.load(root)
    if original["version"] != 1 or original["source_feature"] != value["source_feature"]:
        raise ValueError("source selection requires the unchanged original profile")
    definitions = native_message._definitions({**value, "map": []}, {}, PRIMITIVES, allow_empty=True)
    declaration = definitions.get("Declaration", {})
    if ({row["name"]: row["tag"] for row in declaration.get("cases", [])} != DECLARATIONS
            or definitions.get("EnvironmentObservation", {}).get("cases") != [{"name": "Unavailable", "tag": 1, "type": "Unit"}]):
        raise ValueError("source selection may declare only unmeasured execution")
    lists = [row for row in definitions.values() if row["kind"] == "list"]
    if lists != [{"name": "Strings", "item": "String", "kind": "list"}]:
        raise ValueError("unsupported source declaration repetition")
    available = set(PRIMITIVES)
    for _ in definitions:
        for row in definitions.values():
            references = ([field["type"] for field in row["fields"]] if row["kind"] == "record" else
                          [case["type"] for case in row["cases"]] if row["kind"] == "union" else
                          [row["item"]] if row["kind"] in {"optional", "list"} else [])
            if all(reference in available for reference in references):
                available.add(row["name"])
    if available != set(definitions):
        raise ValueError("source declarations must have a finite nonrecursive grammar")
    for row in definitions.values():
        if row["kind"] == "record" and any(field["name"] in {"purpose", "owner", "operation", "permit", "raw_environment"} for field in row["fields"]):
            raise ValueError("source selection cannot declare execution authority or raw environment")
    return {**value, "types": list(definitions.values())}


def render(model: dict) -> str:
    rows = model["types"]
    index = {row["name"]: n for n, row in enumerate(rows)}
    output = ["// Generated source observation DATA only; no admitted catalog or rights.",
              "use super::{Case, Type};", f'pub(super) const VERSION: u16 = {model["version"]};',
              f'pub(super) const INVOCATION_VERSION: u16 = {model["invocation_version"]};',
              "pub(super) const REGULAR: u16 = 1;", "pub(super) const MEMORY_CLEANUP: u16 = 2;",
              f'pub(super) const DECLARATION: usize = {index["Declaration"]};',
              f'pub(super) const SOURCE_PATH: usize = {index["HandlerSourcePath"]};',
              "pub(super) const TYPES: &[Type] = &["]
    for row in rows:
        kind = row["kind"]
        if kind == "primitive":
            expression = "Type::" + row["name"]
        elif kind == "record":
            expression = "Type::Record(&[" + ", ".join(str(index[field["type"]]) for field in row["fields"]) + "])"
        elif kind == "union":
            expression = "Type::Union(&[" + ", ".join(f'Case {{ tag: {case["tag"]}, item: {index[case["type"]]} }}' for case in row["cases"]) + "])"
        else:
            expression = f'Type::{kind.title()}({index[row["item"]]})'
        output.append(f'    {expression}, // {row["name"]}')
    return "\n".join([*output, "];", ""])

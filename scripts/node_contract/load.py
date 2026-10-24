"""Canonical node data definitions; generated values carry no authority."""
from __future__ import annotations

import hashlib
import json
import keyword
import re
import tomllib
from pathlib import Path

from .common import (
    ENCODINGS,
    FIELD,
    GENERATED_MEMBERS,
    GENERATED_TYPES,
    IDENTIFIER,
    ISSUERS,
    RESERVED,
    keys,
    variant_name,
)
from .fragments import load_fragments
from .lifecycle import adapters, lifecycle
from .operations import load_operations

META = {"version", "status", "unsigned_encoding", "device_id_algorithm", "device_id_domain",
        "device_public_key_encoding", "unknown_fields", "decoded_values"}


def load(root: Path) -> tuple[dict, list[dict], str]:
    if {path.name for path in root.glob("*.toml")} != {"contract.toml", "types.toml", "lifecycle.toml", "adapters.toml", "operations.toml"}:
        raise ValueError("unrecognized contract source file")
    meta = tomllib.loads((root / "contract.toml").read_text())
    keys(meta, META, META)
    if (type(meta["version"]) is not int or meta["version"] != 1 or meta["status"] != "proposal"
            or meta["unsigned_encoding"] != "canonical-decimal-string" or meta["device_id_algorithm"] != "sha256"
            or meta["device_public_key_encoding"] != "raw-ed25519-32-bytes" or meta["unknown_fields"] != "reject"
            or meta["decoded_values"] != "untrusted-data" or not isinstance(meta["device_id_domain"], str)
            or not re.fullmatch(r"[ -~]+\x00", meta["device_id_domain"])):
        raise ValueError("unsupported contract version or encoding")
    source = tomllib.loads((root / "types.toml").read_text())
    keys(source, {"type"}, {"type"})
    if not isinstance(source["type"], list) or not source["type"]:
        raise ValueError("contract has no types")
    fragments = load_fragments(root)
    definitions: dict[str, dict] = {}
    for row in [*source["type"], *(row for _, fragment in fragments for row in fragment["type"])]:
        _definition(row, definitions)
    ordered = _ordered(definitions)
    meta["lifecycle"] = lifecycle(root)
    meta["adapters"] = adapters(root)
    meta["operations"] = load_operations(root, ordered, [row for _, fragment in fragments for row in fragment["operation"]])
    if fragments:
        meta["domains"] = [{"file": name, "version": fragment["version"], "status": fragment["status"]} for name, fragment in fragments]
    canonical = json.dumps({"meta": meta, "types": ordered}, sort_keys=True, separators=(",", ":")).encode()
    return meta, ordered, hashlib.sha256(canonical).hexdigest()


def _definition(row: dict, definitions: dict[str, dict]) -> None:
    kind = row.get("kind") if isinstance(row, dict) else None
    shape = {"name", "kind", "visibility"}
    extra = {"scalar": {"encoding", "issuer", "secret", "min_bytes", "max_bytes", "minimum", "maximum", "unit"}, "record": {"fields"}, "union": {"tag", "variants"}, "optional": {"item"}, "list": {"item", "max_items", "min_items", "unique"}}
    if kind not in extra:
        raise ValueError("unsupported type kind")
    keys(row, shape | extra[kind], shape | (extra[kind] - {"secret", "min_bytes", "max_bytes", "minimum", "maximum", "unit", "min_items", "unique"}))
    name = row["name"]
    if (not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or name in definitions
                or name in GENERATED_TYPES or keyword.iskeyword(name)
            or name.startswith(("Verified", "Authenticated", "Admitted", "Approved"))):
        raise ValueError("duplicate, invalid or authority-bearing DTO name")
    if row["visibility"] not in {"public", "private"}:
        raise ValueError("invalid type visibility")
    if kind == "scalar":
        _scalar(row)
    elif kind in {"optional", "list"}:
        if not isinstance(row["item"], str):
            raise ValueError("invalid collection type reference")
        if kind == "list" and (type(row["max_items"]) is not int or type(row.get("min_items", 0)) is not int
                               or type(row.get("unique", False)) is not bool
                               or not 0 <= row.get("min_items", 0) <= row["max_items"] <= 1024 or row["max_items"] < 1):
            raise ValueError("invalid list bound")
    else:
        members = row["fields" if kind == "record" else "variants"]
        if not isinstance(members, list) or (kind == "union" and not members):
            raise ValueError("invalid type members")
        names = set()
        label = "name" if kind == "record" else "tag"
        if kind == "union" and (not isinstance(row["tag"], str) or not FIELD.fullmatch(row["tag"]) or row["tag"] == "value"):
            raise ValueError("invalid union discriminator")
        for member in members:
            keys(member, {label, "type"}, {label, "type"})
            key = member[label]
            if (not isinstance(key, str) or not FIELD.fullmatch(key) or key in names
                        or keyword.iskeyword(key) or key in RESERVED or key in GENERATED_MEMBERS):
                raise ValueError("duplicate or invalid contract member")
            if not isinstance(member["type"], str):
                raise ValueError("invalid type reference")
            names.add(key)
        if kind == "union" and len({variant_name(member["tag"]) for member in members}) != len(members):
            raise ValueError("generated Rust variant identifiers collide")
    definitions[name] = row



def _scalar(row: dict) -> None:
    encoding = row["encoding"]
    if encoding not in ENCODINGS or row["issuer"] not in ISSUERS or type(row.get("secret", False)) is not bool:
        raise ValueError("invalid scalar definition")
    if encoding == "audit-token-le32" and row["visibility"] != "private":
        raise ValueError("audit token observations remain private")
    if "unit" in row and (row["unit"] != "unix-microseconds" or encoding != "u64" or "minimum" in row or "maximum" in row):
        raise ValueError("unsupported timestamp unit or range")
    if row.get("secret") and row["visibility"] != "private":
        raise ValueError("secret data is not a public client type")
    if encoding in {"text", "utf8", "bytes"}:
        if (type(row.get("min_bytes")) is not int or type(row.get("max_bytes")) is not int
                or not 0 <= row["min_bytes"] <= row["max_bytes"] <= 65536):
            raise ValueError("invalid scalar byte bounds")
    elif "min_bytes" in row or "max_bytes" in row:
        raise ValueError("byte bounds apply only to text, UTF8 content or bytes")
    if "minimum" in row or "maximum" in row:
        ranges = {"u32": (0, 2**32 - 1), "u64": (0, 2**64 - 1), "i32": (-2**31, 2**31 - 1)}
        if (encoding not in ranges or type(row.get("minimum")) is not int or type(row.get("maximum")) is not int
                or not ranges[encoding][0] <= row["minimum"] <= row["maximum"] <= ranges[encoding][1]):
            raise ValueError("invalid scalar numeric bounds")


def _ordered(definitions: dict[str, dict]) -> list[dict]:
    ordered = []
    pending = dict(sorted(definitions.items()))
    while pending:
        available = {row["name"] for row in ordered}
        ready = []
        for name, row in pending.items():
            references = ([row["item"]] if row["kind"] in {"optional", "list"} else
                          [member["type"] for member in row.get("fields", row.get("variants", []))])
            if any(ref not in definitions for ref in references):
                raise ValueError("dangling type reference")
            if row["kind"] == "optional" and definitions[row["item"]]["kind"] not in {"scalar", "record", "union"}:
                raise ValueError("optional items must be nonnullable scalars, records or tagged unions")
            if row["visibility"] == "public" and any(definitions[ref]["visibility"] != "public" for ref in references):
                raise ValueError("private type escapes through a public DTO")
            if all(ref in available for ref in references):
                ready.append(name)
        if not ready:
            raise ValueError("recursive or ambiguous contract definitions")
        ordered.extend(pending.pop(name) for name in ready)
    return ordered

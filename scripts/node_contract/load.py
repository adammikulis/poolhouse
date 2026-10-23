"""Canonical node data definitions; generated values carry no authority."""
from __future__ import annotations

import hashlib
import json
import keyword
import re
import tomllib
from pathlib import Path

META = {"version", "status", "unsigned_encoding", "device_id_algorithm", "device_id_domain",
        "device_public_key_encoding", "unknown_fields", "decoded_values"}
ENCODINGS = {"hex32", "uuid", "u32", "u64", "sid-hex"}
ISSUERS = {"data", "node", "installer", "caller", "producer", "kernel-adapter"}
IDENTIFIER = re.compile(r"[A-Z][A-Za-z0-9]*\Z")
FIELD = re.compile(r"[a-z][a-z0-9_]*\Z")
RESERVED = {"self", "type", "match", "enum", "struct", "impl", "crate", "super", "pub", "ref", "mod", "move"}
GENERATED_MEMBERS = {"to_wire", "from_wire"}
GENERATED_TYPES = {"Self", "String", "Option", "Result", "Vec", "ClassVar", "From", "TryFrom", "Clone", "PartialEq", "Eq", "Hash", "Debug"}


def variant_name(tag: str) -> str:
    return tag.title().replace("_", "")


def keys(value: dict, allowed: set[str], required: set[str]) -> None:
    if not isinstance(value, dict) or value.keys() - allowed or required - value.keys():
        raise ValueError("unknown or missing contract fields")


def load(root: Path) -> tuple[dict, list[dict], str]:
    if {path.name for path in root.glob("*.toml")} != {"contract.toml", "types.toml"}:
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
    definitions: dict[str, dict] = {}
    for row in source["type"]:
        _definition(row, definitions)
    ordered = _ordered(definitions)
    canonical = json.dumps({"meta": meta, "types": ordered}, sort_keys=True, separators=(",", ":")).encode()
    return meta, ordered, hashlib.sha256(canonical).hexdigest()


def _definition(row: dict, definitions: dict[str, dict]) -> None:
    kind = row.get("kind") if isinstance(row, dict) else None
    shape = {"name", "kind", "visibility"}
    extra = {"scalar": {"encoding", "issuer", "secret"}, "record": {"fields"}, "union": {"tag", "variants"}}
    if kind not in extra:
        raise ValueError("unsupported type kind")
    keys(row, shape | extra[kind], shape | (extra[kind] - {"secret"}))
    name = row["name"]
    if (not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or name in definitions
                or name in GENERATED_TYPES or keyword.iskeyword(name)
            or name.startswith(("Verified", "Authenticated", "Admitted", "Approved"))):
        raise ValueError("duplicate, invalid or authority-bearing DTO name")
    if row["visibility"] not in {"public", "private"}:
        raise ValueError("invalid type visibility")
    if kind == "scalar":
        if row["encoding"] not in ENCODINGS or row["issuer"] not in ISSUERS or type(row.get("secret", False)) is not bool:
            raise ValueError("invalid scalar definition")
        if row.get("secret") and row["visibility"] != "private":
            raise ValueError("secret data is not a public client type")
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


def _ordered(definitions: dict[str, dict]) -> list[dict]:
    ordered = []
    pending = dict(sorted(definitions.items()))
    while pending:
        available = {row["name"] for row in ordered}
        ready = []
        for name, row in pending.items():
            references = [member["type"] for member in row.get("fields", row.get("variants", []))]
            if any(ref not in definitions for ref in references):
                raise ValueError("dangling type reference")
            if row["visibility"] == "public" and any(definitions[ref]["visibility"] != "public" for ref in references):
                raise ValueError("private type escapes through a public DTO")
            if all(ref in available for ref in references):
                ready.append(name)
        if not ready:
            raise ValueError("recursive or ambiguous contract definitions")
        ordered.extend(pending.pop(name) for name in ready)
    return ordered

"""Names and closed-field checks shared by contract readers."""
from __future__ import annotations

import re

ENCODINGS = {"hex32", "uuid", "u32", "u64", "sid-hex", "text", "utf8", "bool", "i32", "bytes", "audit-token-le32", "finite-f64-bits"}
ISSUERS = {"data", "node", "installer", "caller", "producer", "kernel-adapter"}
IDENTIFIER = re.compile(r"[A-Z][A-Za-z0-9]*\Z")
FIELD = re.compile(r"[a-z][a-z0-9_]*\Z")
RESERVED = {"self", "type", "match", "enum", "struct", "impl", "crate", "super", "pub", "ref", "mod", "move"}
GENERATED_MEMBERS = {"to_wire", "from_wire"}
GENERATED_TYPES = {"Self", "String", "Option", "Result", "Vec", "ClassVar", "From", "TryFrom", "Clone", "PartialEq", "Eq", "Hash", "Debug", "Any", "MappingProxyType", "ValueError", "RuntimeError", "TypeError"}


def variant_name(tag: str) -> str:
    return tag.title().replace("_", "")


def keys(value: dict, allowed: set[str], required: set[str]) -> None:
    if not isinstance(value, dict) or value.keys() - allowed or required - value.keys():
        raise ValueError("unknown or missing contract fields")



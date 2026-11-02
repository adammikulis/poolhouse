"""Reference-only transcript profile. No signing, authority or runtime acceptance.

The domains below identify new DATA profiles, never native-authority operation v1.
Mathematical signature inputs are explicit; admitted descriptors, production issuer
and native helper acceptance remain unresolved.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .common import keys

PROFILES = MappingProxyType({
    "authority-descriptor-v1": ("AuthorityDescriptorBody", "descriptor_id", "poolhouse.authority.descriptor.v1\0"),
    "approval-immutable-v1": ("ApprovalImmutableOperation", "digest", "poolhouse.approval.immutable-operation.v1\0"),
    "local-authority-scoped-v1": ("LocalAuthorityScopedOperation", "transcript_digest", "poolhouse.local-authority.scoped-operation.v1\0"),
})


def closure(rows: list[dict], root: str) -> list[dict]:
    definitions = {row["name"]: row for row in rows}
    pending, found = [root], set()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        row = definitions[name]
        pending.extend([row["item"]] if row["kind"] in {"list", "optional"} else
                       [field["type"] for field in row.get("fields", row.get("variants", []))])
    return [definitions[name] for name in sorted(found)]


def schema_digest(rows: list[dict], root: str) -> str:
    return hashlib.sha256(json.dumps(closure(rows, root), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load(root: Path, rows: list[dict]) -> dict:
    """Load fixed reference profiles and refuse any unreviewed reachable schema drift."""
    value = tomllib.loads((root / "native_boundaries/transcripts.toml").read_text())
    fields = {"version", "status", "encoding", "signature_input", "signature_issuer", "signature_algorithm", "profile"}
    keys(value, fields, fields)
    if (type(value["version"]) is not int or value["version"] != 1
            or value["status"] != "reference-only" or value["encoding"] != "tagged-wire-tree-be32-v1"
            or value["signature_input"] != "reference-raw-transcript-v1" or value["signature_issuer"] != "unresolved"
            or value["signature_algorithm"] != [
                {"name": "ed25519", "message_api": "transcript-bytes", "digest_api": "unsupported", "format": "raw-64"},
                {"name": "p256_sha256_low_s_der", "message_api": "transcript-bytes",
                 "digest_api": "sha256-transcript-bytes", "format": "strict-der-low-s"}]
            or type(value["profile"]) is not list
            or len(value["profile"]) != len(PROFILES)):
        raise ValueError("unsupported transcript profile")
    seen = set()
    for profile in value["profile"]:
        keys(profile, {"name", "root", "omit_root_field", "domain", "schema_sha256"},
             {"name", "root", "omit_root_field", "domain", "schema_sha256"})
        name = profile["name"]
        if type(name) is not str or name not in PROFILES or name in seen:
            raise ValueError("unsupported transcript purpose")
        seen.add(name)
        if ((profile["root"], profile["omit_root_field"], profile["domain"]) != PROFILES[name]
                or profile["schema_sha256"] != schema_digest(rows, profile["root"])):
            raise ValueError("transcript definition or reachable schema changed")
    return value


def _count(number: int) -> bytes:
    if not 0 <= number <= 0xffffffff:
        raise ValueError("transcript framing overflow")
    return number.to_bytes(4, "big")


def _frame(value: object) -> bytes:
    if value is None:
        return b"N"
    if type(value) is bool:
        return b"T" if value else b"F"
    if type(value) is str:
        encoded = value.encode("utf-8", errors="strict")
        return b"S" + _count(len(encoded)) + encoded
    if type(value) is list:
        return b"A" + _count(len(value)) + b"".join(_frame(item) for item in value)
    if type(value) is dict and all(type(key) is str and key.isascii() for key in value):
        return b"O" + _count(len(value)) + b"".join(_frame(key) + _frame(value[key]) for key in sorted(value))
    raise ValueError("unsupported transcript value")


def _scalar(row: dict, value: object) -> bool:
    from poolhouse.api.node_contract._codec import _valid

    encoding = row["encoding"]
    if encoding == "bool":
        return type(value) is bool
    if type(value) is not str:
        return False
    # Private observation predicates match scripts/node_contract/rust.py VALIDATE.
    if encoding == "audit-token-le32":
        valid = _valid("hex32", value)
    elif encoding == "sid-hex":
        valid = (16 <= len(value) <= 136 and _valid("bytes", value) and value.startswith("01")
                 and int(value[2:4], 16) <= 15 and len(value) == 16 + int(value[2:4], 16) * 8)
    else:
        valid = _valid(encoding, value)
    if not valid:
        return False
    if encoding in {"text", "utf8", "bytes"}:
        length = len(value) // 2 if encoding == "bytes" else len(value.encode("utf-8"))
        if not row["min_bytes"] <= length <= row["max_bytes"]:
            return False
    return "minimum" not in row or row["minimum"] <= int(value) <= row["maximum"]


def _validate(definitions: dict, name: str, value: object) -> None:
    row = definitions[name]
    kind = row["kind"]
    if kind == "scalar":
        if not _scalar(row, value):
            raise ValueError("invalid transcript scalar")
    elif kind == "optional":
        if value is not None:
            _validate(definitions, row["item"], value)
    elif kind == "list":
        if type(value) is not list or not row.get("min_items", 0) <= len(value) <= row["max_items"]:
            raise ValueError("invalid transcript list")
        for item in value:
            _validate(definitions, row["item"], item)
        if row.get("unique") and any(item in value[:index] for index, item in enumerate(value)):
            raise ValueError("duplicate transcript list item")
    elif kind == "record":
        if type(value) is not dict or set(value) != {field["name"] for field in row["fields"]}:
            raise ValueError("unknown or missing transcript fields")
        for field in row["fields"]:
            _validate(definitions, field["type"], value[field["name"]])
    else:
        variants = {variant["tag"]: variant["type"] for variant in row["variants"]}
        if (type(value) is not dict or set(value) != {row["tag"], "value"}
                or type(value[row["tag"]]) is not str or value[row["tag"]] not in variants):
            raise ValueError("invalid transcript union")
        _validate(definitions, variants[value[row["tag"]]], value["value"])


def _no_number(value: str) -> None:
    raise ValueError("transcript numbers must use their canonical wire representation")


@dataclass(frozen=True)
class ReferenceSignatureInput:
    """Untrusted mathematical input, never signer admission or person approval."""

    algorithm: str
    mode: str
    value: bytes


class ReferenceTranscripts:
    """A tooling reference over fixed purposes; values and matching digests grant nothing."""

    def __init__(self, root: Path):
        from .load import load as load_contract
        _, rows, _ = load_contract(root)
        self._profiles = MappingProxyType({profile["name"]: MappingProxyType(profile)
                                            for profile in load(root, rows)["profile"]})
        self._definitions = {row["name"]: row for row in rows}

    def _parsed(self, purpose: str, raw: bytes) -> tuple[dict, dict]:
        from poolhouse.json_objects import unique_object

        if type(purpose) is not str or purpose not in self._profiles or type(raw) is not bytes:
            raise ValueError("unsupported transcript purpose or input")
        profile = self._profiles[purpose]
        try:
            value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=unique_object,
                               parse_int=_no_number, parse_float=_no_number, parse_constant=_no_number)
            _validate(self._definitions, profile["root"], value)
            if purpose == "authority-descriptor-v1":
                from .authority_admission import validate_descriptor
                validate_descriptor(value)
            if purpose == "local-authority-scoped-v1":
                if value["profile_version"] != "1":
                    raise ValueError("unsupported local authority profile")
                if value["scope"]["kind"] == "pool_approval":
                    self._check("approval-immutable-v1", value["scope"]["value"])
        except (UnicodeError, RecursionError, OverflowError) as error:
            raise ValueError("invalid transcript input") from error
        return profile, value

    def _bytes(self, purpose: str, value: dict) -> bytes:
        profile = self._profiles[purpose]
        body = {key: item for key, item in value.items() if key != profile["omit_root_field"]}
        return profile["domain"].encode("ascii") + _frame(body)

    def _check(self, purpose: str, value: dict) -> None:
        expected = hashlib.sha256(self._bytes(purpose, value)).digest()
        supplied = bytes.fromhex(value[self._profiles[purpose]["omit_root_field"]])
        if not hmac.compare_digest(expected, supplied):
            raise ValueError("transcript digest mismatch")

    def transcript_bytes(self, purpose: str, raw: bytes) -> bytes:
        """Compute bytes without asserting that the supplied root digest matches."""
        _, value = self._parsed(purpose, raw)
        return self._bytes(purpose, value)

    def digest_bytes(self, purpose: str, raw: bytes) -> bytes:
        return hashlib.sha256(self.transcript_bytes(purpose, raw)).digest()

    def verify_digest(self, purpose: str, raw: bytes) -> None:
        """Refuse a wrong root or nested digest; never repair the serialized input."""
        _, value = self._parsed(purpose, raw)
        self._check(purpose, value)


    def signature_input(self, purpose: str, raw: bytes, algorithm: str, mode: str = "message") -> ReferenceSignatureInput:
        """Map only a digest-checked operation to an exact algorithm/API input.

        Future issuers must independently admit the descriptor/key, algorithm,
        purpose/profile and native presentation. Matching bytes grant nothing.
        """
        if (type(algorithm) is not str or type(mode) is not str
                or algorithm not in {"ed25519", "p256_sha256_low_s_der"} or mode not in {"message", "digest"}
                or (algorithm == "ed25519" and mode != "message")):
            raise ValueError("unsupported signature input profile")
        self.verify_digest(purpose, raw)
        message = self.transcript_bytes(purpose, raw)
        return ReferenceSignatureInput(algorithm, mode, message if mode == "message" else hashlib.sha256(message).digest())

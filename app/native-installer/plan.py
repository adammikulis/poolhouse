"""Bounded preparation data. A plan never authorizes installation or elevation.

The genuine installer must independently measure its trusted bundled candidates,
freeze their descriptors before approval, and compare them again before writing.
Neither a caller-provided plan nor its digest establishes that provenance.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

MAX_BYTES = 32768
DESTINATIONS = (
    "/Library/Application Support/Poolhouse/Approval",
    "/Library/Application Support/Poolhouse/Authority",
)
REQUIRED_FLAGS = ("hard", "kill", "runtime")
FORBIDDEN_ENTITLEMENTS = (
    "com.apple.security.cs.allow-dyld-environment-variables",
    "com.apple.security.cs.allow-jit",
    "com.apple.security.cs.allow-unsigned-executable-memory",
    "com.apple.security.cs.disable-library-validation",
    "com.apple.security.get-task-allow",
)


def _hex(value, length=64):
    return isinstance(value, str) and re.fullmatch(f"[0-9a-f]{{{length}}}", value) is not None and set(value) != {"0"}


def _object(entries):
    result = {}
    for key, value in entries:
        if key in result:
            raise ValueError("The installation preview is invalid.")
        result[key] = value
    return result


@dataclass(frozen=True)
class Artifact:
    executable_sha256: str
    resources_sha256: str
    cdhash: str

    @classmethod
    def parse(cls, value):
        if not isinstance(value, dict) or set(value) != {"executable_sha256", "resources_sha256", "cdhash"}:
            raise ValueError("The installation preview is invalid.")
        if not _hex(value["executable_sha256"]) or not _hex(value["resources_sha256"]) or not _hex(value["cdhash"], 40):
            raise ValueError("The installation preview is invalid.")
        return cls(**value)


@dataclass(frozen=True)
class Plan:
    """An immutable preview, including when it was parsed from hostile input."""

    source: str
    build: str
    policy: str
    authority: str
    owner_uid: int
    helper: Artifact
    gui: Artifact

    @classmethod
    def parse(cls, data: bytes):
        try:
            if not isinstance(data, bytes) or len(data) > MAX_BYTES:
                raise ValueError
            value = json.loads(data, object_pairs_hook=_object)
            fields = {"version", "source", "build", "policy", "authority", "owner_uid", "helper", "gui",
                      "destinations", "required_flags", "forbidden_entitlements"}
            if not isinstance(value, dict) or set(value) != fields:
                raise ValueError
            if type(value["version"]) is not int or value["version"] != 1:
                raise ValueError
            if any(not _hex(value[key]) for key in ("source", "build", "policy", "authority")):
                raise ValueError
            if type(value["owner_uid"]) is not int or not 0 < value["owner_uid"] < 2**32 - 1:
                raise ValueError
            for key, expected in (("destinations", DESTINATIONS), ("required_flags", REQUIRED_FLAGS),
                                  ("forbidden_entitlements", FORBIDDEN_ENTITLEMENTS)):
                if value[key] != list(expected):
                    raise ValueError
            return cls(*(value[key] for key in ("source", "build", "policy", "authority", "owner_uid")),
                       Artifact.parse(value["helper"]), Artifact.parse(value["gui"]))
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            raise ValueError("The installation preview is invalid.") from None

    def canonical(self) -> bytes:
        value = {key: getattr(self, key) for key in ("source", "build", "policy", "authority", "owner_uid")}
        value.update(version=1, helper=vars(self.helper), gui=vars(self.gui), destinations=DESTINATIONS,
                     required_flags=REQUIRED_FLAGS, forbidden_entitlements=FORBIDDEN_ENTITLEMENTS)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        # Constructors are data constructors too; serialization enforces the same schema.
        if self.parse(encoded) != self:
            raise ValueError("The installation preview is invalid.")
        return encoded

    def digest(self) -> str:
        """Identify preview bytes, without conferring trust on them."""
        return hashlib.sha256(b"poolhouse.native-install-preview.v1\0" + self.canonical()).hexdigest()

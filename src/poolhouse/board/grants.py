"""Standing grants as a client sees them: the grants of a board as a session reads them, and the signing a person's key
does to issue or revoke one (docs/node.md, "Standing grants").

The node counts a grant only when a person key enrolled on it signed the grant, so nothing here can make one for a
caller that does not hold that key. The key is passed in as an object with ``public`` (32 bytes) and ``sign_bytes``.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from poolhouse.board.client import Client
from poolhouse.board.session import Session

__all__ = ["DEFAULT_ACTIONS", "Grant", "Listing", "Signs", "Want", "approve", "canonical", "default_bundle", "duration_ms",
           "enrol", "enrolment", "fingerprint", "issue", "listing", "log", "revoke", "sign_issue", "sign_revoke"]

SIGN_DOMAIN = b"poolhouse/grant/v1\n"
ENROLL_DOMAIN = b"poolhouse/person-enroll/v1\n"
SPANS = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
MOST_DAYS = 366


class Signs(Protocol):
    """A person's key: its public half and a signature over bytes."""

    @property
    def public(self) -> bytes: ...

    def sign_bytes(self, data: bytes) -> bytes: ...


def canonical(value: Any) -> str:
    """``value`` as the node writes it before hashing or signing: sorted keys, no spaces."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(public: bytes) -> str:
    """The name the node knows a person key by: SHA-256 of its public key."""
    return hashlib.sha256(public).hexdigest()


def duration_ms(text: str) -> int:
    """``30d``, ``12h``, ``90m`` or ``45s`` as milliseconds; at most a year."""
    found = re.fullmatch(r"([1-9][0-9]{0,6})([smhd])", text.strip())
    if not found:
        raise ValueError("a time is a whole number and s, m, h or d: 90m, 12h, 30d")
    ms = int(found[1]) * SPANS[found[2]]
    if ms > MOST_DAYS * SPANS["d"]:
        raise ValueError(f"a grant lasts at most {MOST_DAYS} days")
    return ms


def _plain(value: Any) -> None:
    """Refuse text the node would not read back byte for byte: grants are plain ASCII."""
    if isinstance(value, str) and (not value.isascii() or any(ord(c) < 32 for c in value)):
        raise ValueError("a grant is plain ASCII text")
    if isinstance(value, dict):
        for key, item in value.items():
            _plain(key)
            _plain(item)
    elif isinstance(value, list):
        for item in value:
            _plain(item)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _params(key: Signs, body: dict[str, Any], domain: bytes) -> dict[str, Any]:
    _plain(body)
    sig = key.sign_bytes(domain + canonical(body).encode()).hex()
    return {**{k: v for k, v in body.items() if k not in ("op", "board")}, "sig": sig}


@dataclass(frozen=True, slots=True)
class Want:
    """One grant a person asks for: ``to`` is `{"kind": "session", "name": ...}`, a class or an account."""

    action: str
    to: dict[str, Any]
    resource: str = "*"
    device: str = "*"
    lasts_ms: int = 3_600_000
    uses: int | None = None
    review: bool = False


def sign_issue(key: Signs, board: str, want: Want, *, now_ms: int | None = None, gid: str = "") -> dict[str, Any]:
    """The `grant_issue` params for ``want``, signed by ``key``."""
    at = _now_ms() if now_ms is None else now_ms
    body: dict[str, Any] = {"op": "issue", "board": board, "gid": gid or "g-" + secrets.token_hex(8), "to": want.to,
                            "action": want.action, "scope": {"board": board, "device": want.device, "resource": want.resource},
                            "at_ms": at, "until_ms": at + want.lasts_ms, "person": fingerprint(key.public)}
    if want.uses is not None:
        body["uses"] = want.uses
    if want.review:
        body["conditions"] = {"review": True}
    return _params(key, body, SIGN_DOMAIN)


def sign_revoke(key: Signs, board: str, gid: str, reason: str = "", *, now_ms: int | None = None) -> dict[str, Any]:
    """The `grant_revoke` params for the grant ``gid``, signed by ``key``."""
    body: dict[str, Any] = {"op": "revoke", "board": board, "gid": gid, "at_ms": _now_ms() if now_ms is None else now_ms,
                            "person": fingerprint(key.public)}
    if reason:
        body["reason"] = reason
    return _params(key, body, SIGN_DOMAIN)


def issue(session: Session, key: Signs, want: Want) -> dict[str, Any]:
    """Ask the node to put ``want`` on the board, signed by ``key``; the session only carries the request."""
    return dict(session.call("grant_issue", **sign_issue(key, session.board, want)))


def revoke(session: Session, key: Signs, gid: str, reason: str = "") -> dict[str, Any]:
    return dict(session.call("grant_revoke", **sign_revoke(key, session.board, gid, reason)))


def enrolment(key: Signs, about: str, *, now_ms: int | None = None) -> dict[str, Any]:
    """A key's own proof that it holds its private half, to carry to a node: valid for five minutes."""
    body = {"op": "enroll", "public": key.public.hex(), "about": about, "at_ms": _now_ms() if now_ms is None else now_ms}
    _plain(body)
    return {k: v for k, v in body.items() if k != "op"} | {"proof": key.sign_bytes(ENROLL_DOMAIN + canonical(body).encode()).hex()}


def approve(proof: dict[str, Any], approver: Signs) -> dict[str, Any]:
    """The proof of a new key with the approval of a key the node already holds."""
    if not isinstance(proof, dict) or not {"public", "about", "at_ms", "proof"} <= proof.keys():
        raise ValueError("a carried key is what `person-proof` printed: public, about, at_ms and proof")
    body = {"op": "enroll", "public": proof["public"], "about": proof["about"], "at_ms": proof["at_ms"]}
    return {**proof, "by": fingerprint(approver.public), "approval": approver.sign_bytes(ENROLL_DOMAIN + canonical(body).encode()).hex()}


def enrol(client: Client, token: str, params: dict[str, Any]) -> dict[str, Any]:
    """Put a person key on the node: ``params`` from `enrolment` (the first key) or `approve` (every later one)."""
    return dict(client.call("person_enroll", "", token, **params))


@dataclass(frozen=True, slots=True)
class Grant:
    """One grant as the board stands: what was signed, how much was used, and whether it holds."""

    gid: str
    action: str
    to: dict[str, Any]
    scope: dict[str, Any]
    at_ms: int
    until_ms: int
    uses: int | None
    used: int
    review: bool
    status: str
    why: str
    person: str
    entry: str
    carried_by: str
    mine: bool


@dataclass(frozen=True, slots=True)
class Listing:
    grants: list[Grant]
    ignored: list[dict[str, Any]]
    persons: list[str]
    registry: dict[str, list[str]] = field(default_factory=dict)
    now_ms: int = 0
    device: str = ""


def listing(session: Session) -> Listing:
    """Every grant of the session's board with its state, the entries that name none that count, and the registry."""
    got = session.call("grants")
    rows = [Grant(**{k: g[k] for k in Grant.__dataclass_fields__}) for g in got["grants"]]
    return Listing(rows, got["ignored"], got["persons"], got["registry"], got["now_ms"], got["device"])


def log(session: Session, limit: int = 50) -> list[dict[str, Any]]:
    """The newest grant entries: issues, revocations and uses, with who used what for which target."""
    return list(session.call("grant_log", limit=limit)["log"])


DEFAULT_ACTIONS = ("land_control", "task_authority", "shard_consent", "note_verify", "sync_now", "shard_call")
DESCRIPTIONS = {
    "land_control": "pause, resume and cancel others' requests in the landing queue",
    "task_authority": "act for the person on tasks: assign, review in strict mode, set the enforcement mode, record a landing",
    "shard_consent": "turn test shards on or off on this device and name the devices whose tests it takes",
    "note_verify": "record that the command of a note was run and what it printed (subagents included)",
    "sync_now": "ask this device to sync the board with the pool now",
    "shard_call": "run tests on another device of the pool that allowed this device",
}


def default_bundle(family: str, device: str, days: int = 30) -> list[tuple[Want, str]]:
    """The grants a person approves once at setup, each with a line saying what it lets the lead do.

    They name the main sessions of one model family on one device, none for more than ``days`` days, and each one is
    revoked by its own id.
    """
    if not 1 <= days <= MOST_DAYS:
        raise ValueError(f"days is from 1 to {MOST_DAYS}")
    out = []
    for action in DEFAULT_ACTIONS:
        to = {"kind": "class", "family": family, "main": action != "note_verify"}
        out.append((Want(action, to, "*", device, days * SPANS["d"]), DESCRIPTIONS[action]))
    return out

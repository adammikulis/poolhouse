"""Log exchange between members: the server side of pull and push, and the client."""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import Any

from poolhouse.http import ServerError
from poolhouse.workspace import coordinator_client, device_logs_merge as rules, replica_fold
from poolhouse.workspace.identity import AGENT, Denied
from poolhouse.workspace.service import Workspace

__all__ = ["PREFIX", "Target", "answer", "exchange", "joined_targets", "route", "run", "sync"]

PREFIX = "/workspace/v1/replica/"
VERSION = 1
MAX_ROWS = 400
MAX_ROUNDS = 20
MAX_REQUEST = 4 * 1024 * 1024
MAX_BATCH_BYTES = 2 * 1024 * 1024
MAX_ROW_BYTES = 128 * 1024
MAX_REQUEST_ORIGINS = 16


@dataclass(frozen=True, slots=True)
class Target:
    """A member to exchange logs with."""

    fingerprint: str
    member: Any


def _vector(doc: Any) -> dict[str, int]:
    if type(doc) is not dict:
        raise ValueError("a vector is an object")
    out = {}
    for origin, entry in doc.items():
        if not rules.valid_origin(origin) or type(entry) is not dict or type(entry.get("seq")) is not int:
            raise ValueError("a vector entry names an origin and a sequence number")
        out[origin] = entry["seq"]
    return out


def _rows(replica, since: dict[str, int]) -> dict[str, list[dict[str, Any]]]:
    """For each origin the trusted rows past ``since``, starting one row early so the receiver
    can compare it with its copy, within the row and byte limits of one request."""
    out: dict[str, list[dict[str, Any]]] = {}
    rows, size = 0, 0
    for origin in replica.logs.origins():
        trusted = replica.logs.trusted(origin)
        have = since.get(origin, 0)
        if origin in replica.logs.damaged() or have >= len(trusted):
            continue
        for row in trusted[max(0, have - 1):]:
            size += len(json.dumps(row))
            if rows >= MAX_ROWS or (size > MAX_BATCH_BYTES and rows):
                return out
            out.setdefault(origin, []).append(row)
            rows += 1
    return out


def _trusted_vector(replica) -> dict[str, dict[str, Any]]:
    out = {}
    for origin in replica.logs.origins():
        trusted = replica.logs.trusted(origin)
        if trusted:
            out[origin] = {"seq": trusted[-1]["seq"], "hash": trusted[-1]["hash"]}
    return out


def _take(replica, logs: Any, fingerprint: str) -> tuple[int, dict[str, str]]:
    """Store what ``logs`` carries; returns the row count and the origins refused."""
    if type(logs) is not dict or len(logs) > MAX_REQUEST_ORIGINS:
        raise ValueError("logs are a small object")
    stored, refused, total = 0, {}, 0
    for origin, rows in logs.items():
        if not rules.valid_origin(origin) or type(rows) is not list:
            raise ValueError("a log is a list of rows under an origin id")
        total += len(rows)
        if total > 2 * MAX_ROWS or any(len(json.dumps(r)) > MAX_ROW_BYTES for r in rows):
            raise ValueError("the request exceeds its bound")
        try:
            stored += len(replica.logs.ingest(origin, rows, replica.owns(fingerprint, origin)))
        except (rules.Damaged, rules.Gap, rules.Quota) as bad:
            refused[origin] = type(bad).__name__
    return stored, refused


def answer(ws, path: str, document: Any, fingerprint: str) -> dict[str, Any]:
    """The reply to a pull or a push from the member ``fingerprint``."""
    replica = ws.replica
    if type(document) is not dict or document.get("version") != VERSION or not rules.valid_origin(document.get("origin")):
        raise ValueError("a replica request names its version and origin")
    if not replica.bind_member(fingerprint, document["origin"]):
        raise Denied("this device writes a different log than the one it presented before")
    if path == PREFIX + "pull":
        since = _vector(document["vector"])
        replica.acknowledge(fingerprint, document["vector"])
        replica.seal()
        return {"version": VERSION, "origin": replica.origin, "vector": _trusted_vector(replica),
                "logs": _rows(replica, since)}
    stored, refused = _take(replica, document.get("logs"), fingerprint)
    replica_fold.apply(ws)
    return {"version": VERSION, "origin": replica.origin, "stored": stored, "refused": refused,
            "vector": _trusted_vector(replica)}


def route(handler, body: bytes | None) -> bool:
    """Serve /workspace/v1/replica/pull and /push on the authenticated member server."""
    path = handler.path.split("?")[0]
    if path not in (PREFIX + "pull", PREFIX + "push"):
        return False
    secure = isinstance(handler.connection, ssl.SSLSocket) or handler.client_address[0] in ("127.0.0.1", "::1")
    device = getattr(handler, "_workspace_device", None)
    if handler.command != "POST" or not secure or device is None:
        handler._send(403, {"error": "log exchange needs a member over an encrypted connection"})
        return True
    try:
        if len(body or b"") > MAX_REQUEST:
            raise ValueError("the request exceeds its bound")
        handler._send(200, answer(Workspace(), path, json.loads(body or b"{}"), device.fingerprint))
    except Denied:
        handler._send(403, {"error": "log exchange refused"})
    except (ValueError, KeyError, TypeError, OverflowError):
        handler._send(400, {"error": "invalid log exchange"})
    except (OSError, RuntimeError):
        handler._send(503, {"error": "log storage is unavailable"})
    return True


def joined_targets() -> list[Target]:
    """The active members this device can authenticate to."""
    out = []
    for row in coordinator_client._joined_rows():
        try:
            member = coordinator_client._device_member({"endpoint": row["url"], "cert": row["certificate"]})
        except (Denied, KeyError):
            continue
        out.append(Target(str(row["fingerprint"]), member))
    return out


def exchange(ws, target: Target) -> dict[str, Any]:
    """Pull what ``target`` holds that this device lacks, push what it lacks, fold the result."""
    replica = ws.replica
    result: dict[str, Any] = {"pulled": 0, "pushed": 0, "refused": {}, "rewound": False, "signed": replica.seal()}
    remote: dict[str, int] = {}
    for _ in range(MAX_ROUNDS):
        reply = target.member._json("POST", PREFIX + "pull", {
            "version": VERSION, "origin": replica.origin, "vector": replica.logs.vector()})
        if not rules.valid_origin(reply.get("origin")) or not replica.bind_member(target.fingerprint, reply["origin"]):
            raise ValueError("the device presented a different log than before")
        remote = _vector(reply["vector"])
        result["rewound"] = replica.acknowledge(target.fingerprint, reply["vector"]) or result["rewound"]
        stored, refused = _take(replica, reply["logs"], target.fingerprint)
        result["pulled"] += stored
        result["refused"].update(refused)
        if not stored:
            break
    unsent = _rows(replica, remote)
    if unsent:
        reply = target.member._json("POST", PREFIX + "push", {"version": VERSION, "origin": replica.origin,
                                                            "logs": unsent})
        result["pushed"] = int(reply["stored"])
        result["refused"].update({o: str(r)[:40] for o, r in reply["refused"].items()})
        replica.acknowledge(target.fingerprint, reply["vector"])
    replica_fold.apply(ws)
    return result


def sync(ws, targets: list[Target] | None = None) -> list[dict[str, Any]]:
    """Exchange logs with every member; an unreachable device is reported, never raised."""
    out = []
    for target in joined_targets() if targets is None else targets:
        try:
            out.append({"device": target.fingerprint, **exchange(ws, target)})
        except (ServerError, OSError, ValueError, KeyError, TypeError):
            out.append({"device": target.fingerprint, "error": "unreachable or refused"})
    return out


def run(ws, token: str) -> list[dict[str, Any]]:
    """`sync` for the token's owner; an agent token cannot start signing or network calls."""
    if ws.auth(token).role == AGENT:
        raise Denied("a person or a lead exchanges logs")
    return sync(ws)

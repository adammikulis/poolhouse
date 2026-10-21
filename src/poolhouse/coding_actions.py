"""Immutable coding action bindings stored by the conversation graph owner."""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Any

from poolhouse import requests
from poolhouse.activity.source_snapshot import INDEX_LIMIT
from poolhouse.fleet.conversation_types import CodingState
from poolhouse.harnesshook import _summary
from poolhouse.json_objects import unique_object
from poolhouse.requests.model import Origin, make_choices, shown

MAX_FRAME = 4 * 1024 * 1024
TOOLS = ("read", "bash", "edit", "write", "grep", "find", "ls")
IDENTITY = frozenset({"actor", "pool", "device", "native_session"})


def canonical(value: Any, *, limit: int | None = None) -> str:
    """Encode strict JSON without changing object keys or types."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, RecursionError) as error:
        raise ValueError("Coding actions require finite JSON values") from error
    if limit is not None and len(encoded.encode()) > limit:
        raise ValueError("The coding action exceeds its frame limit")
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError("Coding action object keys must be text")
            pending.extend(item.values())
        elif type(item) is list:
            pending.extend(item)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError("Coding actions require JSON values")
    return encoded


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def parsed(raw: bytes) -> dict:
    """Read one bounded coding tool frame, refusing duplicate and nonfinite fields."""
    if len(raw) > MAX_FRAME:
        raise ValueError("The coding action exceeds its frame limit")
    value = json.loads(raw, object_pairs_hook=unique_object)
    canonical(value, limit=MAX_FRAME)
    if type(value) is not dict:
        raise ValueError("A coding action must be an object")
    return value


def fingerprint(value: str) -> str:
    if type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("A coding implementation fingerprint is required")
    return value


def identity(value: dict) -> dict:
    if (type(value) is not dict or set(value) != IDENTITY or any(type(item) is not str or not item
            or len(item) > 256 or not item.isascii() or not item.isprintable() for item in value.values())):
        raise ValueError("An authenticated coding execution identity is required")
    return dict(value)


def policy(value: CodingState) -> dict:
    result = asdict(value)
    result["settings"] = dict(value.settings)
    return result


@dataclass(frozen=True, slots=True)
class Intent:
    message: int
    identity: dict
    workspace: dict
    implementation: str


@dataclass(frozen=True, slots=True)
class Execution:
    policy: CodingState
    id: str
    binding: str
    message: int


@dataclass(frozen=True, slots=True)
class Action:
    id: str
    tool: str
    arguments: str
    workspace: str
    decision: str

    @classmethod
    def prepare(cls, identifier, tool, inputs, workspace, decision) -> Action:
        arguments = call_arguments(identifier, tool, inputs)
        if decision.action not in ("allow", "ask", "deny"):
            raise ValueError("Invalid coding action decision")
        return cls(identifier, tool, arguments, canonical(workspace, limit=INDEX_LIMIT), canonical(asdict(decision)))

    def binding(self, execution: Execution) -> dict:
        return {"run": execution.binding, "id": self.id, "tool": self.tool, "arguments": self.arguments,
                "input": digest(json.loads(self.workspace)), "decision": self.decision}

    def semantic(self, execution: Execution) -> str:
        return digest({key: value for key, value in self.binding(execution).items() if key != "id"})


def request_keys(seed: str, binding: str) -> tuple[str, str]:
    key = bytes.fromhex(fingerprint(seed))
    body = bytes.fromhex(fingerprint(binding))
    return tuple(hmac.new(key, domain + body, hashlib.sha256).hexdigest()
                 for domain in (b"poolhouse/coding/request/v1\0", b"poolhouse/coding/cancel/v1\0"))


def approval_request(run: dict, execution: Execution, action: Action):
    """Build the fixed parent-owned approval words and cancellation capability."""
    row = run["actions"][action.id]
    key, cancellation = request_keys(run["seed"], row["binding"])
    who = run["binding"]["identity"]
    origin = requests.Origin(who["actor"], execution.policy.conversation, who["native_session"], execution.policy.model)
    decision = row["decision"]
    ask = requests.Ask(decision["kind"], f"{action.tool} {_summary(action.tool, json.loads(action.arguments))}",
                       decision["reason"], ("allow-once", "deny"), origin, key=key,
                       extra=(("action_digest", row["binding"]), ("request_binding", key)))
    return ask, cancellation


def approval_matches(request, ask) -> bool:
    """Compare every approval word with its fixed action-bound request."""
    origin = ask.origin
    expected = replace(request, kind=ask.kind, key=ask.key,
        raised_by=Origin(shown(origin.agent, "name"), shown(origin.project, "name"), shown(origin.session, "name"),
                         shown(origin.model, "model"), shown(origin.model_state, "label")),
        subject=shown(ask.subject, "subject"), reason=shown(ask.reason, "reason"),
        choices=make_choices(ask.choices), extra={"action_digest": dict(ask.extra)["action_digest"], "request_binding": ask.key})
    return expected.fingerprint == request.fingerprint


def call_arguments(identifier, tool, inputs) -> str:
    """Validate one fixed Pi tool identity and its canonical JSON arguments."""
    if (type(identifier) is not str or not identifier or len(identifier) > 256
            or not identifier.isascii() or not identifier.isprintable() or type(tool) is not str
            or tool not in TOOLS or type(inputs) is not dict):
        raise ValueError("Invalid coding action")
    return canonical(inputs, limit=MAX_FRAME)


def receipt(row: dict) -> dict:
    """Return a copied action receipt after checking its bounded result digest."""
    if (type(row) is not dict or row.get("state") not in
            ("intent", "attached", "running", "completed", "denied", "expired", "cancelled", "interrupted")):
        raise ValueError("The coding action receipt is unreadable; it stays interrupted")
    if row["state"] == "completed" and (type(row.get("result")) is not dict
            or digest(row["result"]) != row.get("result_digest") or type(row.get("workspace")) is not dict):
        raise ValueError("The completed coding result changed; it stays interrupted")
    if row["state"] == "completed":
        canonical(row["result"], limit=MAX_FRAME)
        canonical(row["workspace"], limit=INDEX_LIMIT)
    return json.loads(canonical(row))

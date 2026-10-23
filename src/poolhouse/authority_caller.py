"""Read-only admission of the current registered agent for delegated operations.

Native ids select credentials, not authority. The live node supplies the role. This
preserves the existing bearer-token boundary; full native producer isolation is separate.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from poolhouse import home, node_health
from poolhouse.credentials.reading import CredentialError, read_text
from poolhouse.person import HumanRequired
from poolhouse.private_path import problem

_COMPONENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_READS = frozenset({"whoami", "agents", "land_accepts"})


class ClientState(Protocol):
    state: Path
    timeout: float


@dataclass(frozen=True, slots=True)
class Identity:
    name: str
    parent: str
    harness: str
    retired: bool
    person: bool
    retired_reason: str = ""


def _identity(raw: dict[str, Any]) -> Identity:
    if (not isinstance(raw, dict) or not isinstance(raw.get("name"), str)
            or not _COMPONENT.fullmatch(raw["name"]) or not isinstance(raw.get("parent"), str)
            or not isinstance(raw.get("harness"), str)
            or type(raw.get("retired")) is not bool or type(raw.get("person")) is not bool):
        raise ValueError("invalid session identity")
    return Identity(raw["name"], raw["parent"], raw["harness"], raw["retired"], raw["person"],
                    str(raw.get("retired_reason", "")))


@dataclass(frozen=True, slots=True)
class ReadConnection:
    state: Path
    timeout: float = node_health.TIMEOUT_S
    autostart: bool = False

    def query(self, method: str, *, board: str = "", token: str = "", **params: Any) -> dict:
        return node_health.result_of(node_health.exchange(
            self.state, node_health.request_body(method, params, board=board, token=token), self.timeout))


@dataclass(frozen=True, slots=True)
class Admission:
    client: ReadConnection
    board: str
    name: str
    token: str = field(repr=False)

    def call(self, method: str, **params: Any) -> Any:
        if method not in _READS or (method == "whoami" and params):
            raise ValueError("caller admission is read-only")
        return self.client.query(method, board=self.board, token=self.token, **params)

    def whoami(self) -> Identity:
        result = self.call("whoami")
        identity = _identity(result["identity"])
        if result["name"] != identity.name or result["board"] != self.board:
            raise ValueError("session token belongs to another board or identity")
        return identity

    def agents(self, retired: bool = False) -> list[Identity]:
        return [_identity(raw) for raw in self.call("agents", retired=retired)["agents"]]


@dataclass(frozen=True, slots=True)
class AuthenticatedAgent:
    session: Admission
    identity: Identity

    @property
    def name(self) -> str:
        return self.identity.name


def _token(state: Path, board: str, name: str) -> str:
    if not _COMPONENT.fullmatch(board) or not _COMPONENT.fullmatch(name):
        raise ValueError("invalid credential location")
    target = state / "client" / board / f"{name}.token"
    if problem(target):
        raise ValueError("session token is not a private regular file")
    return read_text(target, anchor=target.parent).strip()


def _selectors() -> list[tuple[str, str]]:
    fields = (("CODEX_THREAD_ID", "codex"), ("CLAUDE_CODE_SESSION_ID", "claude-code"),
              ("POOLHOUSE_SESSION_ID", os.environ.get("POOLHOUSE_SESSION_HARNESS", "") or "claude-code"),
              ("POOLHOUSE_SUBAGENT", "claude-code"))
    return [(harness, native) for key, harness in fields if (native := os.environ.get(key, "").strip())]


def current(*, cwd: Path | None = None, client: ClientState | None = None) -> AuthenticatedAgent:
    """Authenticate an existing live main agent without registration or starting a node."""
    node = ReadConnection(client.state if client else home.state("node"))
    try:
        board = os.environ.get("POOLHOUSE_BOARD", "") if cwd is None else ""
        if not board:
            board = node.query("project_resolve", path=str((cwd or Path.cwd()).resolve()))["board"]
        if not isinstance(board, str) or not _COMPONENT.fullmatch(board):
            raise ValueError("invalid board")
        names = {node.query("session_lookup", board=board, harness=harness, session=native)["name"]
                 for harness, native in _selectors()}
        named = os.environ.get("POOLHOUSE_WORKSPACE_AGENT", "")
        if len(names) > 1 or (names and named and named not in names):
            raise ValueError("conflicting session selectors")
        name = next(iter(names), named)
        supplied = os.environ.get("POOLHOUSE_WORKSPACE_TOKEN", "").strip()
        token = _token(node.state, board, name) if name else supplied
        actor = Admission(node, board, name, token)
        identity = actor.whoami()
        if name and identity.name != name:
            raise ValueError("token conflicts with selected identity")
        if any(harness != identity.harness for harness, _native in _selectors()):
            raise ValueError("native harness conflicts with identity")
        if supplied and Admission(node, board, name, supplied).whoami().name != identity.name:
            raise ValueError("provided credential conflicts with identity")
    except (OSError, ValueError, KeyError, TypeError, CredentialError):
        raise HumanRequired("could not authenticate the current workspace session") from None
    if identity.retired or identity.parent or identity.person:
        raise HumanRequired("this operation requires an authenticated live main agent, not a helper or a person session")
    return AuthenticatedAgent(Admission(node, board, identity.name, token), identity)

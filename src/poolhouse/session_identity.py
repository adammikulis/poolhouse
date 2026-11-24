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
from poolhouse.private_path import problem

_COMPONENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_READS = frozenset({"whoami", "agents", "land_accepts"})


class Unauthenticated(PermissionError):
    """The current workspace session could not be identified: it names no registered live main agent."""


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
        raise ValueError("the board or agent name is not a valid file name")
    target = state / "client" / board / f"{name}.token"
    if problem(target):
        raise _Conflict("the session token file is not a private regular file",
                        "make it private with `chmod 600` on the file in the node's client directory, or "
                        + _DOCTOR)
    return read_text(target, anchor=target.parent).strip()


def _selectors() -> list[tuple[str, str]]:
    fields = (("CODEX_THREAD_ID", "codex"), ("CLAUDE_CODE_SESSION_ID", "claude-code"),
              ("POOLHOUSE_SESSION_ID", os.environ.get("POOLHOUSE_SESSION_HARNESS", "") or "claude-code"),
              ("POOLHOUSE_SUBAGENT", "claude-code"))
    return [(harness, native) for key, harness in fields if (native := os.environ.get(key, "").strip())]


_LOOKUP = "looking up this session"
_DOCTOR = "run `poolhouse-workspace doctor` in this session"
_SELECTORS = "CODEX_THREAD_ID, CLAUDE_CODE_SESSION_ID, POOLHOUSE_SESSION_ID and POOLHOUSE_SUBAGENT"


class _Conflict(ValueError):
    """Two things that name the session disagree; ``fix`` says which to drop."""

    def __init__(self, what: str, fix: str) -> None:
        super().__init__(what)
        self.fix = fix


def _why(error: Exception) -> str:
    """Why a step failed, from the kind of failure and the messages this module and the node write.

    Never a token, a path or an environment value: those are not in the exceptions' text, and the
    kinds without a message of ours are named by what they mean.
    """
    if isinstance(error, node_health.Refused):
        return f"the node refused: {str(error)[:120]}"
    if isinstance(error, OSError):
        return "the node did not answer (it is not running, or its socket cannot be reached)"
    if isinstance(error, ValueError):
        return str(error)
    if isinstance(error, CredentialError):
        return "the token file could not be read as a private credential"
    return "the node's answer was not in the shape expected"


def _unauthenticated(step: str, error: Exception) -> Unauthenticated:
    """The refusal for a failed ``step``: what was being done, why it failed, and the command to run."""
    fix = error.fix if isinstance(error, _Conflict) else _DOCTOR
    if step == _LOOKUP and isinstance(error, node_health.Refused):
        fix = (f"keep only the session id variable of the harness this agent runs in ({_SELECTORS}), or "
               + _DOCTOR)
    return Unauthenticated(f"could not authenticate the current workspace session while {step}: "
                           f"{_why(error)}; {fix}")


def _authenticated(*, cwd: Path | None = None, client: ClientState | None = None) -> AuthenticatedAgent:
    """Read the authenticated identity; this alone grants no delegated authority."""
    node = ReadConnection(client.state if client else home.state("node"))
    step = "finding the project's board"
    try:
        board = os.environ.get("POOLHOUSE_BOARD", "") if cwd is None else ""
        if not board:
            board = node.query("project_resolve", path=str((cwd or Path.cwd()).resolve()))["board"]
        if not isinstance(board, str) or not _COMPONENT.fullmatch(board):
            raise ValueError("the board name is not valid")
        step = _LOOKUP
        names = {node.query("session_lookup", board=board, harness=harness, session=native)["name"]
                 for harness, native in _selectors()}
        named = os.environ.get("POOLHOUSE_WORKSPACE_AGENT", "")
        if len(names) > 1 or (names and named and named not in names):
            raise _Conflict("the session ids and POOLHOUSE_WORKSPACE_AGENT name different agents",
                            f"unset all but one of {_SELECTORS}, and POOLHOUSE_WORKSPACE_AGENT")
        name = next(iter(names), named)
        supplied = os.environ.get("POOLHOUSE_WORKSPACE_TOKEN", "").strip()
        step = "reading the session token"
        token = _token(node.state, board, name) if name else supplied
        step = "asking the node who the token belongs to"
        actor = Admission(node, board, name, token)
        identity = actor.whoami()
        if name and identity.name != name:
            raise _Conflict("the token belongs to a different agent than the one selected",
                            "unset POOLHOUSE_WORKSPACE_AGENT, or " + _DOCTOR)
        if any(harness != identity.harness for harness, _native in _selectors()):
            raise _Conflict("the session ids name a different harness than the one this agent registered with",
                            f"unset all but one of {_SELECTORS}")
        step = "checking POOLHOUSE_WORKSPACE_TOKEN"
        if supplied and Admission(node, board, name, supplied).whoami().name != identity.name:
            raise _Conflict("POOLHOUSE_WORKSPACE_TOKEN belongs to a different agent than the session",
                            "unset POOLHOUSE_WORKSPACE_TOKEN")
    except (OSError, ValueError, KeyError, TypeError, CredentialError) as error:
        raise _unauthenticated(step, error) from None
    return AuthenticatedAgent(Admission(node, board, identity.name, token), identity)


def _not_main(identity: Identity) -> str:
    """Why this session is not a live main agent, and who can run the operation instead."""
    if identity.retired:
        return (f"the session `{identity.name}` is retired and cannot be revived; start a new session, "
                f"or {_DOCTOR}")
    if identity.parent:
        return (f"`{identity.name}` is a helper of `{identity.parent}`; ask `{identity.parent}` to run it, "
                "or ask for a grant with `poolhouse-workspace approval request`")
    return (f"`{identity.name}` is a person's session; a person runs this from a terminal that no agent "
            "started")


def current(*, cwd: Path | None = None, client: ClientState | None = None) -> AuthenticatedAgent:
    """Authenticate an existing live main agent without registration or starting a node."""
    actor = _authenticated(cwd=cwd, client=client)
    if actor.identity.retired or actor.identity.parent or actor.identity.person:
        raise Unauthenticated("this operation needs the identity of a live main agent session, not a "
                              f"helper: {_not_main(actor.identity)}")
    return actor


def is_live_child(*, cwd: Path, asserted_name: str = "") -> bool:
    """Whether automatic work belongs to the caller's coordinator; grants no authority."""
    try:
        actor = _authenticated(cwd=cwd)
    except Unauthenticated:
        return False
    return bool(actor.identity.parent and not actor.identity.retired and not actor.identity.person and
                (not asserted_name or asserted_name == actor.name))

"""A local model that joins a board as a session: its record, its status file and who it obeys.

The folder ``local-agents/`` under the workspace state holds, per agent NAME, ``NAME.json`` (what the
person asked for), ``NAME.status.json`` (what the loop is doing), ``NAME.stop`` (the kill
switch the loop checks every step) and ``NAME.log``. No file holds a token: the worker's token is
the one its parent's registration left in the client folder of the node (`board.credentials`).
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from poolhouse import home, node_launch, roles
from poolhouse.board import credentials, grants, person
from poolhouse.board.client import Client
from poolhouse.board.names import valid_id, valid_name
from poolhouse.board.session import Agent as Registered, Session
from poolhouse.chatpolicy import APPROVE_FIRST, PLAN_AND_GO, READ_ONLY
from poolhouse.files import read_json, write_json
from poolhouse.serve.process import pid_exists, started_at

__all__ = ["APPROVE_FIRST", "DEFAULT_ORDERS_FROM", "ORDER_KINDS", "PLAN_AND_GO", "READ_ONLY",
           "Agent", "Status", "alive", "check_name", "check_orders", "check_project", "folder",
           "is_person", "leads", "load", "log_file", "manages", "names", "native_id", "obeys", "pause_file", "role_choices", "save",
           "session_of", "status_of", "stop_file"]

DEFAULT_ORDERS_FROM: tuple[str, ...] = ()
ORDER_KINDS = ("task", "question")
MOST_ORDERERS = 8
DEPTH = 8
RESERVED = frozenset({"admin", "system", "human", "workspace", "owner", "root", "setup", "agent"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f‪-‮⁦-⁩]")


def folder(base: Path) -> Path:
    """The directory of local-agent files, owner only."""
    path = base / "local-agents"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def role_choices() -> list[dict[str, str]]:
    """Every role as ``{"name", "summary"}``, in the order of `roles.ROLES`."""
    return [{"name": r.name, "summary": r.summary} for r in roles.ROLES.values()]


def check_name(name: str) -> str:
    """``name`` when it can be a local agent's id; ValueError otherwise."""
    if not valid_name(name) or name in RESERVED or name.startswith(("poolhouse", "doctor-")) \
            or len(name) > 40:
        raise ValueError(f"{name!r} cannot be an agent name (a-z, 0-9, . _ -; up to 40; "
                         f"not a reserved word)")
    return name


def check_project(path: str, base: Path) -> str:
    """The resolved folder ``path`` names, or an empty string for none; ValueError for text that
    is not a plain existing folder or one that holds the workspace's own state."""
    if not path:
        return ""
    if _CONTROL.search(path) or len(path) > 1024:
        raise ValueError("the project path holds control characters or is too long")
    found = Path(path).expanduser().resolve()
    if not found.is_dir():
        raise ValueError(f"the project {found.name} is not a folder")
    here = base.resolve()
    if found == here or here in found.parents:
        raise ValueError("the project cannot be the workspace's own state folder")
    return str(found)


def check_orders(names: list[str]) -> tuple[str, ...]:
    """The sessions the person named as givers of orders; ValueError for a name that is no session name."""
    unique = tuple(dict.fromkeys(n.strip() for n in names if n.strip()))
    if len(unique) > MOST_ORDERERS or not all(valid_id(n) for n in unique):
        raise ValueError(f"name at most {MOST_ORDERERS} sessions, each by the name `agents` shows")
    return unique


@dataclass(frozen=True, slots=True)
class Agent:
    """What the person asked for when the agent was started; ``identity`` is the name its session has on ``board``."""

    name: str
    model: str
    identity: str = ""
    board: str = ""
    model_name: str = ""
    size_bytes: int = 0
    role: str = roles.DEFAULT
    profile: str = "chat"
    harness: str = "poolhouse-agent"
    ctx: int = 32768
    effort: str = "off"
    max_effort: str = "medium"
    project: str = ""
    orders_from: tuple[str, ...] = DEFAULT_ORDERS_FROM
    started: float = 0.0
    pid: int = 0
    process_started: float = 0.0
    log: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    max_output_tokens: int | None = None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["orders_from"] = list(self.orders_from)
        return out


def native_id(starter: str, name: str) -> str:
    """The session id this device registers the agent ``name`` under for the session ``starter`` that starts it: each device,
    each starter and each agent has a board name of its own."""
    return f"{home.machine_id()}-{starter}-{name}"


def session_of(agent: Agent, client: Client | None = None) -> Session:
    """The agent's own session on its board, with the token its registration left in the client folder."""
    node = client or Client(node_launch.default_state())
    return Session(node, agent.board, credentials.load(node.state, agent.board, agent.identity), agent.identity)


def _paths(base: Path, name: str) -> dict[str, Path]:
    where = folder(base)
    return {"record": where / f"{name}.json", "status": where / f"{name}.status.json",
            "stop": where / f"{name}.stop", "log": where / f"{name}.log"}


def pause_file(base: Path, name: str) -> Path:
    """The file that pauses the worker before its next queued task."""
    return folder(base) / f"{check_name(name)}.pause"


def stop_file(base: Path, name: str) -> Path:
    """The file whose presence tells ``name``'s loop to stop at its next step."""
    return _paths(base, check_name(name))["stop"]


def log_file(base: Path, name: str) -> Path:
    return _paths(base, check_name(name))["log"]



def save(base: Path, agent: Agent) -> None:
    """Write ``agent``'s record."""
    path = _paths(base, check_name(agent.name))["record"]
    write_json(path, {"version": 1, **agent.as_dict()})
    path.chmod(0o600)


def load(base: Path, name: str) -> Agent | None:
    """The record of ``name``, or None when there is none or it is not one of ours."""
    row = read_json(_paths(base, check_name(name))["record"], None)
    if not isinstance(row, dict) or row.get("version") != 1 or row.get("name") != name:
        return None
    fields = {k: row[k] for k in Agent.__dataclass_fields__ if k in row}
    fields["orders_from"] = tuple(str(n) for n in fields.get("orders_from", ()))
    if fields.get("max_output_tokens") == 8192 and not fields.get("extra", {}).get("explicit_output_limit"):
        fields["max_output_tokens"] = None
    return Agent(**fields)


def names(base: Path) -> list[str]:
    """Every agent with a record."""
    return sorted(p.name.removesuffix(".json") for p in folder(base).glob("*.json")
                  if not p.name.endswith(".status.json") and valid_name(p.name.removesuffix(".json")))


def alive(agent: Agent) -> bool:
    """Whether the process recorded for ``agent`` is still the one that was started."""
    if not pid_exists(agent.pid):
        return False
    began = started_at(agent.pid)
    return not (began and agent.process_started and abs(began - agent.process_started) > 5.0)


class Status:
    """The loop's own status file: its state, what it last did and when it last looked."""

    def __init__(self, base: Path, name: str) -> None:
        self.base = base
        self.path = _paths(base, name)["status"]
        self.data: dict[str, Any] = read_json(self.path, {}) or {}

    def update(self, **fields: Any) -> None:
        self.data = {**self.data, **fields, "beat": time.time(), "pid": os.getpid()}
        write_json(self.path, self.data)
        self.path.chmod(0o600)


def status_of(base: Path, name: str) -> dict[str, Any]:
    """What the loop of ``name`` last wrote: ``state``, ``detail``, ``steps``, ``tasks`` and so on."""
    found = read_json(_paths(base, check_name(name))["status"], {})
    return found if isinstance(found, dict) else {}


def is_person(s: Session) -> bool:
    """Whether ``s`` is a session of the person's own screen or terminal."""
    me = next((a for a in s.agents() if a.name == s.name), None)
    return bool(me and not me.parent and me.harness == person.HARNESS and me.model == person.MODEL)


def manages(s: Session, agent: Agent) -> bool:
    """Whether ``s`` is the session that started ``agent`` or the person's own session."""
    mine = next((a for a in s.agents() if a.name == agent.identity), None)
    return bool(mine and (mine.parent == s.name or is_person(s)))


def leads(s: Session) -> frozenset[str]:
    """The sessions with no parent that hold a live grant of the task authority for themselves: the person's leads."""
    held = grants.listing(s).grants
    return frozenset(g.to["name"] for g in held if g.action == "task_authority" and g.status == "live"
                     and g.to.get("kind") == "session" and g.to.get("name"))


def _giver(agent: Agent, known: dict[str, Registered], lead: Callable[[], frozenset[str]], sender: str) -> str:
    """Why ``sender``, or a session above it, is one the agent acts for; an empty string when none is."""
    starter = known[agent.identity].parent if agent.identity in known else ""
    name, seen = sender, set()
    while name and name not in seen and len(seen) < DEPTH:
        seen.add(name)
        who = known.get(name)
        if who is None or who.retired:
            return ""
        reason = ""
        if not who.parent and who.harness == person.HARNESS and who.model == person.MODEL:
            reason = "the person"
        elif name in agent.orders_from:
            reason = f"{name}, whom the person named"
        elif name == starter:
            reason = f"{name}, which started it"
        elif not who.parent and name in lead():
            reason = f"{name}, a lead the person gave the task authority"
        if reason:
            return reason if name == sender else f"a delegate of {name}, {reason}"
        name = who.parent
    return ""


def obeys(s: Session, agent: Agent, row: dict[str, Any], known: dict[str, Registered] | None = None) -> str:
    """Why the agent acts on message ``row``, or an empty string when it is information only.

    Only a clear ``task`` or ``question`` from the person's own session, the session that started the agent, a session
    with no parent that holds the task authority, a session the person named, or a subagent of one of those is acted
    on; a message to ``*`` and one held in quarantine never is, and a message on a board must name the agent."""
    sender = str(row.get("from", ""))
    if row.get("state") != "clear" or row.get("type") not in ORDER_KINDS or sender == agent.identity \
            or row.get("to") == "*":
        return ""
    if str(row.get("to", "")).startswith("#") and f"@{agent.identity}" not in str(row.get("raw") or ""):
        return ""
    roster = known if known is not None else {a.name: a for a in s.agents(True)}
    cache: list[frozenset[str]] = []

    def lead() -> frozenset[str]:
        if not cache:
            cache.append(leads(s))
        return cache[0]

    return _giver(agent, roster, lead, sender)

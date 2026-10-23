"""The delegable authority registry: which gates a lead agent may pass and which stay a person's."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import home, person
from poolhouse.authority_caller import current as current_agent
from poolhouse.files import read_json, write_json
from poolhouse.person import HumanRequired, require_person

__all__ = [
    "DELEGATED",
    "FLOOR_ENV",
    "GATES",
    "PERSON",
    "PRESETS",
    "Asking",
    "Scope",
    "audit_rows",
    "current_agent",
    "record",
    "require",
    "resolve",
    "set_state",
    "show",
    "state_of",
]

DELEGATED = "delegated"
PERSON = "person"
FLOOR_ENV = "POOLHOUSE_AUTHORITY_FLOOR"
AGENT_ENV = "POOLHOUSE_WORKSPACE_AGENT"
SUBAGENT_ENV = "POOLHOUSE_SUBAGENT"
DELEGATING = ("CLAUDECODE", "POOLHOUSE_AGENT")

GATES: dict[str, str] = {
    "workspace.setup": "workspace",
    "workspace.model": "workspace",
    "workspace.agents": "workspace",
    "sentinel.quarantine": "sentinel",
    "sentinel.policy": "sentinel",
    "requests.answer": "requests",
    "memory.admin": "memory",
    "reputation.admin": "reputation",
    "activity.export": "activity",
    "fleet.recovery": "fleet",
    "serve.wired-limit": "serve",
    "chat.policy": "chat",
    "runtime.deploy": "runtime",
    "github.release": "github",
    "runtime.deploy-unmerged": "runtime",
}
"""Each delegable gate and its group."""

MEANS: dict[str, str] = {
    "workspace.setup": "install the agent hooks on this device",
    "workspace.model": "record which model a session runs",
    "workspace.agents": "start, stop and replace local agents",
    "sentinel.quarantine": "release or purge what the sentinel quarantined",
    "sentinel.policy": "change the sentinel's mode",
    "requests.answer": "answer the requests that are waiting for you",
    "memory.admin": "administer the memory store",
    "reputation.admin": "change reputation records",
    "activity.export": "export the activity log",
    "fleet.recovery": "export the pool's recovery file",
    "serve.wired-limit": "raise the memory limit models may use",
    "chat.policy": "change what the chat refuses",
    "runtime.deploy": "deploy a new runtime build on this device",
    "github.release": "publish a reviewed development tip or prerelease through the repository App",
    "runtime.deploy-unmerged": "deploy an independently reviewed exact source commit on this device",
}
"""What a lead agent may do without asking the person while a gate is delegated."""

PROD_DELEGATED = frozenset({"workspace.setup", "workspace.model", "workspace.agents"})
PRESETS = ("dev", "prod")


def _file():
    return home.state("authority.json")


def _read() -> dict[str, Any]:
    data = read_json(_file(), {})
    return data if isinstance(data, dict) else {}


def state_of(gate: str, project: str = "") -> str:
    """``person`` or ``delegated`` for ``gate``: the project's setting, else the machine's, else delegated."""
    if gate not in GATES:
        raise KeyError(f"no authority gate called {gate}")
    if os.environ.get(FLOOR_ENV) == PERSON:
        return PERSON
    data = _read()
    scoped = (data.get("projects") or {}).get(project, {}) if project else {}
    default = PERSON if data.get("preset") == "prod" and gate not in PROD_DELEGATED else DELEGATED
    return scoped.get(gate) or (data.get("gates") or {}).get(gate) or default


def resolve(names: Iterable[str]) -> list[str]:
    """The gates ``names`` select: ``ALL``, a group, or a gate; unknown names raise `KeyError`."""
    out: list[str] = []
    for name in names:
        if name == "ALL":
            found = list(GATES)
        elif name in GATES:
            found = [name]
        else:
            found = [g for g, group in GATES.items() if group == name]
        if not found:
            raise KeyError(f"no authority gate or group called {name}")
        out += [g for g in found if g not in out]
    return out


def show(project: str = "") -> dict[str, Any]:
    """Every gate with its group and state, and the last preset applied."""
    data = _read()
    return {"preset": data.get("preset", "dev"), "project": project,
            "gates": [{"gate": g, "group": group, "state": state_of(g, project)} for g, group in GATES.items()]}


def _log(entry: Mapping[str, Any]) -> None:
    path = home.state("authority-audit.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.time(), **entry}) + "\n")


def record(event: str, **fields: Any) -> None:
    """Append an ``event`` a person took to the audit log, with ``fields``."""
    _log({"event": event, "by": PERSON, **fields})


def audit_rows() -> list[dict[str, Any]]:
    """Every recorded flip and delegated use, oldest first."""
    path = home.state("authority-audit.jsonl")
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line] if path.exists() else []


@dataclass(frozen=True, slots=True)
class Scope:
    """Where a change applies: a project (empty for the machine) and the preset it came from."""

    project: str = ""
    preset: str = ""
    cwd: Path | None = None


@dataclass(frozen=True, slots=True)
class Asking:
    """How a loosening reaches the person: the terminal flags and environment `require_person` reads, and the typed line."""

    terminal: tuple[bool, bool] | None = None
    env: Mapping[str, str] | None = None
    typed: Callable[[str], str] | None = None


def _ask_to_loosen(gates: list[str], asking: Asking) -> None:
    """Raise `HumanRequired` unless a person at a terminal types `yes` to the list of what ``gates`` would let a lead do."""
    require_person("loosening " + ", ".join(gates), asking.terminal, asking.env)
    lines = "\n".join(f"  - {MEANS[g]} ({g})" for g in gates)
    prompt = f"A lead agent would be able to do this without asking you:\n{lines}\nType yes to allow it: "
    if (asking.typed or person.typed)(prompt).strip().lower() != "yes":
        raise HumanRequired("loosening not confirmed: nothing was changed")


def set_state(names: Iterable[str], new: str, *, by: str, scope: Scope | None = None,
              asking: Asking | None = None) -> list[dict[str, str]]:
    """Set the selected gates to ``new`` for the machine (or ``project``) and audit who changed what.
    Returns each gate's change; a helper identity (``parent/name``) is refused, and moving a gate from `person` to
    `delegated` is made only by a person at a terminal who types `yes` to what it allows."""
    if "/" in by:
        raise HumanRequired("authority is flipped by a lead agent or a person, not a helper")
    if new not in (PERSON, DELEGATED):
        raise ValueError(f"authority state must be {PERSON} or {DELEGATED}")
    project, preset = (scope.project, scope.preset) if scope else ("", "")
    gates = resolve(names)
    before = {g: state_of(g, project) for g in gates}
    loosened = [g for g in gates if new == DELEGATED and before[g] == PERSON]
    if loosened:
        _ask_to_loosen(loosened, asking or Asking())
    data = _read()
    target = data.setdefault("projects", {}).setdefault(project, {}) if project else data.setdefault("gates", {})
    target.update(dict.fromkeys(gates, new))
    if preset:
        data["preset"] = preset
    write_json(_file(), data)
    changes = [{"gate": g, "from": before[g], "to": new} for g in gates]
    _log({"event": "authority.set", "by": by, "project": project, "gates": gates,
          "from": sorted(set(before.values())), "to": new, "preset": preset})
    return changes


def require(gate: str, action: str, terminal: tuple[bool, bool] | None = None,
            env: Mapping[str, str] | None = None, *, scope: Scope | None = None) -> str:
    """Authenticate a delegated main agent; keep person authorization separate.
    Raises `HumanRequired` otherwise; each delegated pass is audited."""
    scope = scope or Scope()
    if state_of(gate, scope.project) == PERSON:
        require_person(action, terminal, env)
        return PERSON
    found = os.environ if env is None else env
    context = (*person.AGENT_MARKERS, AGENT_ENV, SUBAGENT_ENV, "POOLHOUSE_WORKSPACE_TOKEN", "POOLHOUSE_SESSION_ID")
    if not any(os.environ.get(key) or found.get(key) for key in context):
        require_person(action, terminal, env)
        return PERSON
    caller = current_agent(cwd=scope.cwd)
    if (found.get(SUBAGENT_ENV) or any(found.get(key) and found[key] != caller.name for key in (AGENT_ENV,))):
        raise HumanRequired(f"{action}: supplied workspace identity conflicts with the authenticated caller")
    _log({"event": "authority.use", "gate": gate, "action": action, "agent": caller.name,
          "project": scope.project})
    return DELEGATED

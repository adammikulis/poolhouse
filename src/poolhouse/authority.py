"""The authority registry: which gates an agent passes and which are held back from agents, decided automatically.

A gate is `delegated` (an agent passes it; each pass is audited) or `person` (an agent is refused at once with the reason; a process
with no agent marker passes). Nothing waits for a person, prompts or needs a terminal. Narrowing a gate is open to everyone; loosening
one is refused to an agent with the reason, and runs directly for a process with no agent marker (`poolhouse.caller`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse import home
from poolhouse.caller import AgentRefused, audit_rows, marked, record, refuse_agent, who
from poolhouse.files import read_json, write_json
from poolhouse.session_identity import current as current_agent

__all__ = [
    "DELEGATED",
    "FLOOR_ENV",
    "GATES",
    "PERSON",
    "PRESETS",
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
    "memory.admin": "memory",
    "reputation.admin": "reputation",
    "activity.export": "activity",
    "autostart.install": "autostart",
    "chat.policy": "chat",
    "runtime.deploy": "runtime",
    "github.release": "github",
    "runtime.deploy-unmerged": "runtime",
    "isolation.grant": "isolation",
}
"""Each delegable gate and its group."""

MEANS: dict[str, str] = {
    "workspace.setup": "install the agent hooks on this device",
    "workspace.model": "record which model a session runs",
    "workspace.agents": "start, stop and replace local agents",
    "sentinel.quarantine": "release or purge what the sentinel quarantined",
    "sentinel.policy": "change the sentinel's mode",
    "memory.admin": "administer the memory store",
    "reputation.admin": "change reputation records",
    "activity.export": "export the activity log",
    "autostart.install": "install or roll back the login units that keep Poolhouse running",
    "chat.policy": "change what the chat refuses",
    "runtime.deploy": "deploy a new runtime build on this device",
    "github.release": "publish a reviewed development tip or prerelease through the repository App",
    "runtime.deploy-unmerged": "deploy an independently reviewed exact source commit on this device",
    "isolation.grant": "launch a coding agent with a wider reach than the default (a folder, a toolchain, a host, a file to publish, in place)",
}
"""What a lead agent may do without asking the person while a gate is delegated."""

PROD_DELEGATED = frozenset({"workspace.setup", "workspace.model", "workspace.agents"})
PRESETS = ("dev", "prod")


def _file():
    return home.guarded("authority.json")


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


@dataclass(frozen=True, slots=True)
class Scope:
    """Where a change applies: a project (empty for the machine) and the preset it came from."""

    project: str = ""
    preset: str = ""
    cwd: Path | None = None


def set_state(names: Iterable[str], new: str, *, by: str, scope: Scope | None = None,
              env: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    """Set the selected gates to ``new`` for the machine (or ``project``) and audit who changed what.
    Returns each gate's change. A helper identity (``parent/name``) is refused, and so is an agent that loosens a gate from `person`
    to `delegated`: an agent narrows its own authority and never widens it. A process with no agent marker may do either."""
    if "/" in by:
        raise AgentRefused("authority is flipped by a lead agent or a user, not a helper")
    if new not in (PERSON, DELEGATED):
        raise ValueError(f"authority state must be {PERSON} or {DELEGATED}")
    project, preset = (scope.project, scope.preset) if scope else ("", "")
    gates = resolve(names)
    before = {g: state_of(g, project) for g in gates}
    loosened = [g for g in gates if new == DELEGATED and before[g] == PERSON]
    if loosened:
        refuse_agent("loosening " + ", ".join(loosened),
                     "an agent may narrow its own authority and never widen it: " + "; ".join(f"{MEANS[g]} ({g})" for g in loosened), env)
    data = _read()
    target = data.setdefault("projects", {}).setdefault(project, {}) if project else data.setdefault("gates", {})
    target.update(dict.fromkeys(gates, new))
    if preset:
        data["preset"] = preset
    write_json(_file(), data)
    changes = [{"gate": g, "from": before[g], "to": new} for g in gates]
    record("authority.set", by=by, project=project, gates=gates, **{"from": sorted(set(before.values()))}, to=new, preset=preset)
    return changes


def require(gate: str, action: str, env: Mapping[str, str] | None = None, *, scope: Scope | None = None, attested: str = "") -> str:
    """Decide ``gate`` for ``action`` at once: the state it is in, or `AgentRefused` for an agent when the gate is held back from agents.
    Every pass is audited with who ran it, the agent's authenticated name when the node knows the session. ``attested`` names an agent the
    node found in the caller's process ancestry, which counts as an agent whatever the environment says."""
    scope = scope or Scope()
    state = state_of(gate, scope.project)
    if state == PERSON:
        refuse_agent(action, f"the gate {gate} is held back from agents (`poolhouse-workspace authority show`)", env, attested=attested)
        return PERSON
    if marked(env) or attested:
        record("authority.use", who=who(env), gate=gate, action=action, agent=_agent_name(scope), project=scope.project)
    else:
        record("authority.use", gate=gate, action=action, project=scope.project)
    return DELEGATED


def _agent_name(scope: Scope) -> str:
    """The authenticated session's name when the node knows it, else empty: a gate does not wait on the node."""
    try:
        return current_agent(cwd=scope.cwd).name
    except (PermissionError, OSError, ValueError):
        return ""

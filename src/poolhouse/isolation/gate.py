"""The launcher's choke point: who may widen a coding session, the checks on every launch argument, the log and the sentinel's signal.

One function, `decide_launch`, runs before any launch argument reaches a launcher's parser (docs/design-isolated-launcher.md, 8.3). It
decides each widening at once and never waits: a process the operating system reports as confined is refused every control, an agent that is
not the lead is refused every widening argument, the `isolation.grant` gate is read at once (`delegated` passes only the lead, `person`
passes only a terminal outside any agent), and then the grants are decided under the caps, logged, and reported to the sentinel as one
signal. A project the launch works in that the node does not hold is registered by the launch for the lead and for a terminal outside any agent
(`projects`): naming a project is the base reach and widens nothing. Nothing is typed, confirmed or pasted, and no token is issued.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from poolhouse import activity, authority, caller, home
from poolhouse.contracts.loader import ContractError
from poolhouse.isolation import ancestry, egress, policy, projects, toolchain
from poolhouse.isolation.grants import Decision, Grant, Request, Rule, decide, fingerprint
from poolhouse.isolation.refusals import IsolationRefused, sentence
from poolhouse.sandbox.confinement import confined
from poolhouse.sandbox.run import HOOKS

__all__ = ["GATE", "Arguments", "Launch", "check_on", "decide_launch", "registered_by_the_node", "remove_project", "require_widening"]

GATE = "isolation.grant"
Events = Callable[[str, dict[str, object]], None]
SHOWN = 80
"""How much of a launcher argument a sentence repeats: enough to name it, never a line of the caller's own text."""


@dataclass(frozen=True, slots=True)
class Arguments:
    """What a launcher's command line asks of a launch beyond the default reach (8.2): the widening arguments, and the launcher controls."""

    read: tuple[str, ...] = ()
    toolchain: tuple[str, ...] = ()
    allow_net: tuple[str, ...] = ()
    in_place: bool = False
    project: str = ""
    protect: tuple[str, ...] = ()
    on: str = ""
    parent: str = ""
    source: str = "task"
    """Where the launching session says these arguments come from: the task the person gave it, or a project policy the person owns."""

    @property
    def widening(self) -> bool:
        """Whether any argument widens the session or names the project and the paths the launcher protects."""
        return bool(self.read or self.toolchain or self.allow_net or self.in_place or self.project or self.protect)

    @property
    def controls(self) -> bool:
        """Whether the caller passed any launcher control at all: a confined process may pass none."""
        return self.widening or bool(self.on or self.parent)

    def requests(self, issuer: str, project: str) -> tuple[Request, ...]:
        """One request for each widening the arguments name."""
        named = [("read", self.read), ("toolchain", self.toolchain), ("net", self.allow_net),
                 ("in-place", (project,) if self.in_place else ())]
        return tuple(Request(kind, subject, issuer, self.source, project) for kind, subjects in named for subject in subjects)


@dataclass(frozen=True, slots=True)
class Launch:
    """What a launch was decided to be: every decision, the grants in force and whether the session is wider than the default reach."""

    decisions: tuple[Decision, ...]
    grants: tuple[Grant, ...]
    widened: bool
    unusual: bool


def _short(text: str) -> str:
    return "".join(ch if ch.isprintable() else "?" for ch in text)[:SHOWN]


def check_on(url: str) -> None:
    """Refuse a model server that is not on a numeric loopback address, for every caller (8.3)."""
    try:
        host = urlparse(url).hostname or ""
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise IsolationRefused(sentence("on_not_loopback", url=_short(url)))


def registered_by_the_node(path: str) -> bool:
    """Whether the project registry of the running device holds a project that contains ``path`` (a worktree of a registered project does);
    a registry that cannot answer holds nothing."""
    return bool(policy.key_of(path))


def _refuse(action: str, key: str) -> caller.AgentRefused:
    """The refusal of ``action`` with the sentence ``key``, audited; a confinement that cannot write the audit log is refused all the same."""
    with contextlib.suppress(OSError):
        caller.record("refused", action=action, why=key)
    return caller.AgentRefused(sentence(key))


def _attest() -> ancestry.Ancestry:
    """Who runs this process, as the node attests it from the process tree; a node that cannot say refuses the launch at once, audited."""
    try:
        return ancestry.attest()
    except ancestry.Unattested as why:
        with contextlib.suppress(OSError):
            caller.record("refused", action="attesting who runs the launcher", why="unattested")
        raise caller.AgentRefused(str(why)) from why


def _caller(action: str) -> ancestry.Ancestry:
    """Who runs this process for ``action``: a process inside a confinement is refused whatever its markers say, and the rest is read from the
    process tree by the node."""
    if confined():
        raise _refuse(action, "confined_caller")
    return _attest()


@home.real_roots
def remove_project(target: str, env: Mapping[str, str] | None = None) -> str:
    """Forget the registered project ``target`` names (its id, or a folder of it) and return its board, at once. Only the lead and a terminal
    outside every agent may: any other agent, a process inside a confinement or a node that cannot say who asks is refused, audited."""
    action = "removing a project of this computer"
    if not _caller(action).may_launch(env):
        raise _refuse(action, "not_the_lead")
    return projects.remove(target, issuer=caller.who(env))


@home.real_roots
def require_widening(action: str, env: Mapping[str, str] | None = None, project: str = "") -> ancestry.Ancestry:
    """The `isolation.grant` gate, decided at once. A process inside a confinement is refused whatever its markers say. Whether the caller is an
    agent, the lead or a terminal is read from its process tree by the node (`ancestry`), and the environment only narrows that. With the gate
    `person` (the `prod` preset) an agent is refused, the lead included, and a terminal outside any agent passes; with it `delegated` only
    the lead passes among the agents: any other agent is refused, whether it is a subagent, a tool, an agent the node does not know, or an
    agent that runs the launcher outside the owner's sandbox. The ancestry the decision was made on is returned."""
    where = _caller(action)
    scope = authority.Scope(project=project)
    seen = "an agent's process tree runs it" if where.agent else ""
    if authority.state_of(GATE, project) == authority.PERSON:
        authority.require(GATE, action, env, scope=scope, attested=seen)
        return where
    if not where.may_launch(env):
        raise _refuse(action, "not_the_lead")
    authority.require(GATE, action, env, scope=scope, attested=seen)
    return where


def _controls(arguments: Arguments) -> None:
    """The checks of the launcher controls, which sit here and not in each launcher: the server (for every caller) and the protected paths."""
    if arguments.on:
        check_on(arguments.on)
    for value in arguments.protect:
        if "://" in value and urlparse(value).hostname not in ("127.0.0.1", "::1"):
            raise IsolationRefused(f"Refused: {_short(value)} names something outside this computer.")


def _unregistered(arguments: Arguments, project: str, registered: Callable[[str], bool]) -> dict[str, str]:
    """The folders the launch works in (the one it names and the one it runs in) that the node does not hold, each with where the launch got it."""
    asked = os.path.realpath(arguments.project) if arguments.project else ""
    named = dict.fromkeys(os.path.realpath(each) for each in (arguments.project, project) if each)
    return {each: "named" if each == asked else "working directory" for each in named if not registered(each)}


def _admit_projects(missing: dict[str, str], where: ancestry.Ancestry | None, env: Mapping[str, str] | None, on_event: Events | None) -> None:
    """Make every folder of ``missing`` a registered project: the node adds a git project it does not hold, for the lead and for a terminal outside
    any agent, because naming a project is the base reach of a launch and widens nothing (8.3). What no session is given (`places`) is refused with
    its reason, and any other agent works only in a project the node already holds."""
    if missing and confined():
        raise _refuse("registering a project", "confined_caller")
    if missing and not (where or _attest()).may_launch(env):
        raise _refuse("registering a project", "project_unregistered")
    for named, source in missing.items():
        projects.register(named, issuer=caller.who(env), source=source, emit=lambda name, fields: _emit(name, fields, on_event))


def _log(decision: Decision, request: Request) -> None:
    activity.record("isolation.grant", subject=f"{request.kind}:{fingerprint(request)}", outcome="granted" if decision.granted else "refused",
                    meta={"issuer": request.issuer, "source": request.source, "cause": decision.cause, "line": decision.line})


def _emit(name: str, fields: Mapping[str, object], on_event: Events | None) -> None:
    """Send one signal to the sentinel and to the launcher's own callback; a listener that fails changes nothing for the launch."""
    for call in (HOOKS.events, on_event):
        if call is not None:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                call(name, dict(fields))


def _signal(grants: tuple[Grant, ...], unusual: bool, on_event: Events | None) -> None:
    """One signal to the sentinel for a widened launch: the kinds, the digests of the subjects, the source and the issuer. In place, and a
    network host beside a published file, are marked unusual (AF7)."""
    kinds = sorted({grant.kind for grant in grants})
    _emit("isolation.widened", {"severity": "warning" if unusual else "notice", "policy": "isolation", "kinds": kinds, "unusual": unusual,
                                "subjects": [grant.fingerprint for grant in grants], "source": sorted({grant.source for grant in grants}),
                                "issuer": grants[0].issuer}, on_event)


@home.real_roots
def decide_launch(arguments: Arguments, *, project: str, env: Mapping[str, str] | None = None, on_event: Events | None = None,
                  registered: Callable[[str], bool] | None = None) -> Launch:
    """Decide every widening of one launch at once, or refuse the launch with the reason.

    Raises `AgentRefused` for a confined caller and for a caller the `isolation.grant` gate does not pass (or one the node cannot attest), and
    `IsolationRefused` for a launcher control the deputy checks refuse. The grants it returns are scoped to the one session this launch starts."""
    if confined() and arguments.controls:
        raise _refuse("launching a coding agent with launcher controls", "confined_caller")
    where = require_widening("launching a coding agent with a wider reach than the default", env, project) if arguments.widening else None
    _controls(arguments)
    _admit_projects(_unregistered(arguments, project, registered or registered_by_the_node), where, env, on_event)
    issuer = caller.who(env)
    requests = (*arguments.requests(issuer, project),
                *(Request("toolchain", each.subject, issuer, "declaration", project) for each in toolchain.declarations(project)))
    try:
        listed = egress.hosts()
    except ContractError:
        listed = {}
    rule = Rule(project, policy.load(project), listed, requests)
    decisions = tuple(decide(each, rule) for each in requests)
    for each, request in zip(decisions, requests, strict=True):
        _log(each, request)
    grants = tuple(each.grant for each in decisions if each.grant is not None)
    kinds = {each.kind for each in grants}
    unusual = "in-place" in kinds
    if grants:
        _signal(grants, unusual, on_event)
    return Launch(decisions, grants, bool(grants), unusual)

"""The launcher's choke point: who may widen a coding session, the checks on every launch argument, the log and the sentinel's signal.

One function, `decide_launch`, runs before any launch argument reaches a launcher's parser (docs/design-isolated-launcher.md, 8.3). It
decides each widening at once and never waits: a process the operating system reports as confined is refused every control, an agent that is
not the lead is refused every widening argument, the `isolation.grant` gate is read at once (`delegated` passes only the lead, `person`
passes only a terminal outside any agent), and then the grants are decided under the caps, logged, and reported to the sentinel as one
signal. Nothing is typed, confirmed or pasted, and no token is issued.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

from poolhouse import activity, authority, caller, node_launch, session_identity
from poolhouse.contracts.loader import ContractError
from poolhouse.isolation import egress, policy, toolchain
from poolhouse.isolation.grants import Decision, Grant, Request, Rule, decide, fingerprint
from poolhouse.isolation.refusals import IsolationRefused, sentence
from poolhouse.sandbox.confinement import confined
from poolhouse.sandbox.run import HOOKS

__all__ = ["GATE", "Arguments", "Launch", "check_on", "decide_launch", "registered_by_the_node", "require_widening"]

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
    publish: tuple[str, ...] = ()
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
        return bool(self.read or self.toolchain or self.allow_net or self.publish or self.in_place or self.project or self.protect)

    @property
    def controls(self) -> bool:
        """Whether the caller passed any launcher control at all: a confined process may pass none."""
        return self.widening or bool(self.on or self.parent)

    def requests(self, issuer: str, project: str) -> tuple[Request, ...]:
        """One request for each widening the arguments name."""
        named = [("read", self.read), ("toolchain", self.toolchain), ("net", self.allow_net), ("publish", self.publish),
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
    """Whether the project registry of the running device holds ``path``; a registry that cannot answer holds nothing."""
    try:
        session_identity.ReadConnection(node_launch.default_state()).query("project_resolve", path=os.path.realpath(path))
    except (OSError, ValueError, KeyError, PermissionError):
        return False
    return True


def _is_lead() -> bool:
    """Whether the caller is the live main agent session the node knows, as the authority registry resolves it from the caller's own
    session and not from the project it launches: a subagent, a retired session and a caller the node does not know are not."""
    try:
        authority.current_agent()
    except (PermissionError, OSError, ValueError):
        return False
    return True


def _refuse(action: str, key: str) -> caller.AgentRefused:
    """The refusal of ``action`` with the sentence ``key``, audited; a confinement that cannot write the audit log is refused all the same."""
    with contextlib.suppress(OSError):
        caller.record("refused", action=action, why=key)
    return caller.AgentRefused(sentence(key))


def require_widening(action: str, env: Mapping[str, str] | None = None, project: str = "") -> None:
    """The `isolation.grant` gate, decided at once. A process inside a confinement is refused whatever its markers say. With the gate
    `person` (the `prod` preset) an agent is refused, the lead included, and a terminal outside any agent passes; with it `delegated`
    only the lead passes among the agents: any other marked agent is refused, whether it is a subagent, a tool, or an agent that
    runs the launcher outside the owner's sandbox."""
    if confined():
        raise _refuse(action, "confined_caller")
    scope = authority.Scope(project=project)
    if authority.state_of(GATE, project) == authority.PERSON:
        authority.require(GATE, action, env, scope=scope)
        return
    if caller.marked(env) and not _is_lead():
        raise _refuse(action, "not_the_lead")
    authority.require(GATE, action, env, scope=scope)


def _deputy(arguments: Arguments, registered: Callable[[str], bool], agent: bool) -> None:
    """The checks of a launcher run by an agent, which sit here and not in each launcher: the project, the protected paths and the server.
    The server is checked for every caller."""
    if arguments.on:
        check_on(arguments.on)
    if arguments.project and agent:
        real = os.path.realpath(arguments.project)
        temporary = os.path.realpath(tempfile.gettempdir())
        if not (registered(real) or real == temporary or real.startswith(temporary + os.sep)):
            raise IsolationRefused("Refused: this folder is not a registered project of this computer.")
    for value in arguments.protect:
        if "://" in value and urlparse(value).hostname not in ("127.0.0.1", "::1"):
            raise IsolationRefused(f"Refused: {_short(value)} names something outside this computer.")


def _log(decision: Decision, request: Request) -> None:
    activity.record("isolation.grant", subject=f"{request.kind}:{fingerprint(request)}", outcome="granted" if decision.granted else "refused",
                    meta={"issuer": request.issuer, "source": request.source, "cause": decision.cause, "line": decision.line})


def _signal(grants: tuple[Grant, ...], unusual: bool, on_event: Events | None) -> None:
    """One signal to the sentinel for a widened launch: the kinds, the digests of the subjects, the source and the issuer. In place, and a
    network host beside a published file, are marked unusual (AF7)."""
    kinds = sorted({grant.kind for grant in grants})
    fields: dict[str, object] = {"severity": "warning" if unusual else "notice", "policy": "isolation", "kinds": kinds, "unusual": unusual,
                                 "subjects": [grant.fingerprint for grant in grants], "source": sorted({grant.source for grant in grants}),
                                 "issuer": grants[0].issuer}
    for call in (HOOKS.events, on_event):
        if call is not None:
            with contextlib.suppress(OSError, ValueError, RuntimeError):
                call("isolation.widened", dict(fields))


def decide_launch(arguments: Arguments, *, project: str, env: Mapping[str, str] | None = None, on_event: Events | None = None,
                  registered: Callable[[str], bool] = registered_by_the_node) -> Launch:
    """Decide every widening of one launch at once, or refuse the launch with the reason.

    Raises `AgentRefused` for a confined caller and for a caller the `isolation.grant` gate does not pass, and `IsolationRefused` for a
    launcher control the deputy checks refuse. The grants it returns are scoped to the one session this launch starts."""
    if confined() and arguments.controls:
        raise _refuse("launching a coding agent with launcher controls", "confined_caller")
    if arguments.widening:
        require_widening("launching a coding agent with a wider reach than the default", env, project)
    _deputy(arguments, registered, bool(caller.marked(env)))
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
    unusual = "in-place" in kinds or {"net", "publish"} <= kinds
    if grants:
        _signal(grants, unusual, on_event)
    return Launch(decisions, grants, bool(grants), unusual)

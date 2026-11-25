"""Grants: the five kinds of widening of a coding session, and the one function that decides each at once.

`decide(request, rule)` returns a `Decision` that is granted or refused and nothing else: nothing waits, nothing is asked and nothing is
queued (docs/design-isolated-launcher.md, 8.2 and 8.3). The caps hold for every caller and every source: a governing file is never
published, the project is never changed in place together with a network host, a host is only one the registry names with its source and
its measured bound, and a folder is never a credential location or an ancestor of the state root or of the person's home. The decision
is scoped to the one session it names and issues no token.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from poolhouse import secretpaths
from poolhouse.contracts.loader import ContractError
from poolhouse.isolation import egress, places, publish, toolchain
from poolhouse.isolation.policy import ProjectPolicy
from poolhouse.isolation.refusals import DECISION_LINES, KIND_PHRASES, refusal_line, sentence

__all__ = ["KINDS", "SOURCES", "Decision", "Grant", "Request", "Rule", "decide", "fingerprint"]

KINDS = tuple(KIND_PHRASES)
SOURCES = ("task", "policy")
"""Where a widening may come from: the task the person gave the launching session, or a project policy the person owns (8.3). Anything
else (a board message, a patch, a project file, a refusal sentence, a tool result) is never a source. The one other source is
``declaration``, which only a toolchain folder the project declares may claim."""


@dataclass(frozen=True, slots=True)
class Request:
    """One widening asked for: its kind, its subject, who asked and the source the launching session claims."""

    kind: str
    subject: str
    issuer: str
    source: str = "task"
    project: str = ""


@dataclass(frozen=True, slots=True)
class Rule:
    """What a decision is made under: the registered project, the person's policy for it (None when there is none), the registry of hosts,
    every request of the same launch (two widenings that must not meet are judged together) and the session the decision is scoped to."""

    project: str
    policy: ProjectPolicy | None = None
    registry: Mapping[str, egress.Host] | None = None
    launch: Sequence[Request] = ()
    scope: str = ""


@dataclass(frozen=True, slots=True)
class Grant:
    """A widening in force for one session: what was decided, by whom and from which claimed source, and the object it is bound to."""

    kind: str
    subject: str
    scope: str
    issuer: str
    source: str
    fingerprint: str
    line: str
    held: toolchain.Resolved | publish.PublishRule | None = None


@dataclass(frozen=True, slots=True)
class Decision:
    """Granted or refused, with the line to log and print. ``cause`` says which cap or check decided; it is for the record and the
    tests, and is never part of what a session is told."""

    granted: bool
    line: str
    cause: str
    grant: Grant | None = None


def fingerprint(request: Request) -> str:
    """A short digest that names a request in the record without copying its subject."""
    return hashlib.sha256("\0".join((request.kind, request.subject, request.project)).encode()).hexdigest()[:16]


def _refused(request: Request, cause: str) -> Decision:
    if request.source == "declaration" and cause == toolchain.NOT_INSTALLED:
        return Decision(False, sentence("declared_elsewhere"), cause)
    return Decision(False, refusal_line(request.kind) if request.kind in KIND_PHRASES else "Refused: that is not allowed for this coding session.",
                    cause)


def _granted(request: Request, rule: Rule, subject: str, held: toolchain.Resolved | publish.PublishRule | None = None) -> Decision:
    line = DECISION_LINES[request.kind].format(subject=subject)
    return Decision(True, line, "granted", Grant(request.kind, subject, rule.scope, request.issuer, request.source, fingerprint(request),
                                                 line, held))


def _folder(request: Request, rule: Rule, *, program: bool) -> Decision:
    """A read or a toolchain: the folder resolved once, every refusal run on that object, and the grant built from the same object."""
    held = toolchain.resolve(request.subject)
    if held is None:
        return _refused(request, "not a folder that can be opened without following a link")
    if why := places.never(held.path):
        held.close()
        return _refused(request, why)
    if program and not any(os.access(each, os.X_OK) for each in Path(held.path).glob("bin/*") if each.is_file()):
        held.close()
        return _refused(request, "holds no program")
    walked = toolchain.reach(held.path) if program else (held.path,)
    if (hit := next(filter(None, (secretpaths.tree_hit(Path(each), suffixes=not program) for each in walked)), None)) is not None:
        held.close()
        return _refused(request, "holds a credential or is too large to check: " + hit.rule)
    return _granted(request, rule, held.path, held)


def _file(request: Request, rule: Rule) -> Decision:
    try:
        bound = publish.bind(rule.project, request.subject)
    except publish.NotPublishable as why:
        return _refused(request, str(why))
    except OSError:
        return _refused(request, "the file cannot be read")
    return _granted(request, rule, bound.relpath, bound)


def _decided(request: Request, rule: Rule) -> Decision:
    kinds = {each.kind for each in rule.launch}
    if request.kind == "in-place":
        return _refused(request, "in-place with a network host") if "net" in kinds else _granted(request, rule, os.path.realpath(rule.project))
    if request.kind == "net":
        if "in-place" in kinds:
            return _refused(request, "in-place with a network host")
        listed = egress.hosts() if rule.registry is None else rule.registry
        if not egress.valid_name(request.subject) or request.subject not in listed:
            return _refused(request, "not on the registry list")
        return _granted(request, rule, request.subject)
    if request.kind == "publish":
        return _file(request, rule)
    return _folder(request, rule, program=request.kind == "toolchain")


def _sourced(request: Request, rule: Rule) -> str:
    """Why the claimed source of ``request`` does not hold, or empty. A toolchain the project declares is granted by the declaration; every
    other request names the task or a project policy, and a policy that is present bounds both."""
    if request.source == "declaration":
        found = [each for each in toolchain.declarations(rule.project) if each.subject == request.subject] if request.kind == "toolchain" else []
        return (found[0].refused or "") if found else "not a source of a widening"
    if request.source not in SOURCES:
        return "not a source of a widening"
    named = rule.policy is not None and rule.policy.names(request.kind, request.subject)
    if request.source == "policy" and not named:
        return "no policy names it"
    return "outside the project policy" if rule.policy is not None and not named else ""


def decide(request: Request, rule: Rule) -> Decision:
    """The decision on ``request`` under ``rule``, made at once: granted with its line, or refused with the one refusal form of its kind.

    The claimed source must be a source of a widening; a source that says it is the project policy must be backed by a policy that
    names the subject; and when a policy is present it bounds every request, so a widening it does not name is refused whoever asks."""
    if request.kind not in KINDS:
        return _refused(request, "unknown kind")
    if why := _sourced(request, rule):
        return _refused(request, why)
    try:
        return _decided(request, rule)
    except (OSError, ContractError) as why:
        return _refused(request, f"unreadable: {why.__class__.__name__}")

"""Validate lifecycle and opaque adapter contract declarations."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from . import adapters as native_adapters
from .common import FIELD, keys

CHECK = re.compile(r"[a-z][a-z0-9-]*\Z")


def lifecycle(root: Path) -> dict:
    value = tomllib.loads((root / "lifecycle.toml").read_text())
    fields = {"version", "authorization", "repeated_completion", "cancellation_race", "restart", "session", "invocation", "requirements"}
    keys(value, fields, fields)
    if (value["version"] != 1 or type(value["version"]) is not int or value["authorization"] != "node-checked-code"
            or value["repeated_completion"] != "return-identical-result-without-effects"
            or value["cancellation_race"] != "first-atomic-terminal-transition-wins"
            or value["restart"] != "invalidate-prior-ephemeral-generations"):
        raise ValueError("unsupported lifecycle semantics")
    requirements = value["requirements"]
    required = {"admit", "reserve", "bind", "release", "consume", "cancel", "retire", "invalidate"}
    keys(requirements, required, required)
    for checks in requirements.values():
        if not isinstance(checks, list) or not checks or any(not isinstance(check, str) or not CHECK.fullmatch(check) for check in checks) or len(set(checks)) != len(checks):
            raise ValueError("invalid lifecycle requirements")
    for domain in ("session", "invocation"):
        _domain(value[domain], requirements)
        _semantics(domain, value[domain], requirements)
    if "atomic-single-consumer" not in requirements["consume"] or "single-use-hold" not in requirements["release"]:
        raise ValueError("lifecycle omits single-use ownership")
    return value


def _domain(value: dict, requirements: dict) -> None:
    keys(value, {"states", "initial", "terminal", "transitions"}, {"states", "initial", "terminal", "transitions"})
    states = value["states"]
    terminal = value["terminal"]
    if (not isinstance(states, list) or not states or any(not isinstance(state, str) or not FIELD.fullmatch(state) for state in states)
            or len(set(states)) != len(states) or value["initial"] not in states or not isinstance(terminal, list)
            or not terminal or any(state not in states for state in terminal) or value["initial"] in terminal):
        raise ValueError("invalid lifecycle states")
    if not isinstance(value["transitions"], list) or not value["transitions"]:
        raise ValueError("missing lifecycle transitions")
    seen = set()
    for transition in value["transitions"]:
        keys(transition, {"from", "event", "to"}, {"from", "event", "to"})
        source, event, target = (transition[key] for key in ("from", "event", "to"))
        if source not in states or target not in states or source in terminal or event not in requirements or (source, event) in seen:
            raise ValueError("invalid, duplicate or terminal lifecycle transition")
        seen.add((source, event))
    reachable = {value["initial"]}
    for _ in states:
        reachable.update(row["to"] for row in value["transitions"] if row["from"] in reachable)
    if reachable != set(states):
        raise ValueError("unreachable lifecycle state")



def _semantics(domain: str, value: dict, requirements: dict) -> None:
    if domain == "session":
        states, initial, terminal = {"opening", "live", "retired"}, "opening", {"retired"}
        expected = {("opening", "admit", "live"), ("opening", "retire", "retired"), ("live", "retire", "retired")}
    else:
        states = {"reserved", "bound", "released", "consumed", "cancelled", "invalidated"}
        initial, terminal = "reserved", {"consumed", "cancelled", "invalidated"}
        expected = {("reserved", "bind", "bound"), ("bound", "release", "released"), ("released", "consume", "consumed")}
        expected.update((state, event, target) for state in ("reserved", "bound", "released")
                        for event, target in (("cancel", "cancelled"), ("invalidate", "invalidated")))
    actual = {(row["from"], row["event"], row["to"]) for row in value["transitions"]}
    if (set(value["states"]) != states or value["initial"] != initial or set(value["terminal"]) != terminal
            or len(value["terminal"]) != len(terminal) or actual != expected):
        raise ValueError("unsupported lifecycle v1 state or transition semantics")
    required_checks = {
        "admit": {"authenticated-producer", "actual-native-birth", "validated-root-and-direct-parent", "current-generations"},
        "reserve": {"live-session", "current-generations", "immutable-actor-context", "event-and-purpose", "exact-payload-digest"},
        "bind": {"actual-held-process", "live-exact-process-handle", "reservation-owner", "current-generations", "no-prior-binding"},
        "release": {"live-session", "current-generations", "unchanged-context-purpose-and-digest", "exact-live-bound-process", "single-use-hold"},
        "consume": {"live-session", "current-generations", "exact-live-bound-caller", "matching-event-purpose-and-digest", "required-cleanup-evidence", "atomic-single-consumer"},
        "cancel": {"authorized-caller", "atomic-terminal-race", "close-capabilities-before-termination"},
        "retire": {"invalidate-outstanding-descendants-and-invocations", "close-channels-before-reaping", "new-generation-to-reopen"},
        "invalidate": {"bind-failure-or-process-exit-or-lost-producer-or-lost-holder-or-restart", "no-process-reassignment"},
    }
    if any(set(requirements[event]) != checks for event, checks in required_checks.items()):
        raise ValueError("unsupported lifecycle v1 authorization requirements")

def adapters(root: Path) -> dict:
    return native_adapters.load(root)


def render(value: dict, adapter: dict) -> str:
    output = ['// Generated contract metadata. Transitions do not authorize operations.\nuse super::contract::ProcessObservation;\n',
              '#[derive(Clone, Copy, Debug, PartialEq, Eq)]\npub struct Transition { pub domain: &\'static str, pub from: &\'static str, pub event: &\'static str, pub to: &\'static str, pub checks: &\'static [&\'static str] }\n',
              'pub const TRANSITIONS: &[Transition] = &[']
    for domain in ("session", "invocation"):
        for row in value[domain]["transitions"]:
            checks = ", ".join('"' + check + '"' for check in value["requirements"][row["event"]])
            output.append(f'Transition {{ domain: "{domain}", from: "{row["from"]}", event: "{row["event"]}", to: "{row["to"]}", checks: &[{checks}] }},')
    output.append('];\n#[derive(Debug)]\npub enum NativeAdapterError { ' + ", ".join(adapter["errors"]) + ' }\n')
    output.append(native_adapters.render(adapter))
    return "\n".join(output)

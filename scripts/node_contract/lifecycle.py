"""Validate lifecycle and opaque adapter contract declarations."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .common import FIELD, GENERATED_TYPES, IDENTIFIER, RESERVED, keys

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


def adapters(root: Path) -> dict:
    value = tomllib.loads((root / "adapters.toml").read_text())
    fields = {"version", "trait", "binding", "serialization", "handles", "errors", "requirements", "method"}
    keys(value, fields, fields)
    if (value["version"] != 1 or type(value["version"]) is not int or value["trait"] != "NativeCallerPlatform"
            or value["binding"] != "node-owned-runtime-handle" or value["serialization"] != "forbidden"):
        raise ValueError("unsupported adapter contract")
    for name in ("handles", "errors"):
        items = value[name]
        if not isinstance(items, list) or not items or any(not isinstance(item, str) or not IDENTIFIER.fullmatch(item) or item in GENERATED_TYPES for item in items) or len(set(items)) != len(items):
            raise ValueError("invalid adapter identifiers")
    expected_handles = {"VerifiedInstallation", "RawChannel", "AdmittedProducer", "VerifiedBootstrap", "HeldProcess", "RunningProcess", "KernelCaller"}
    if set(value["handles"]) != expected_handles:
        raise ValueError("invalid opaque handle boundary")
    if not isinstance(value["requirements"], list) or not value["requirements"] or any(not isinstance(check, str) or not CHECK.fullmatch(check) for check in value["requirements"]):
        raise ValueError("invalid adapter requirements")
    known = {*value["handles"], "ProcessObservation", "bool", "unit"}
    methods = {}
    for method in value["method"]:
        keys(method, {"name", "arguments", "result"}, {"name", "arguments", "result"})
        name = method["name"]
        if not isinstance(name, str) or not FIELD.fullmatch(name) or name in RESERVED or name in methods or method["result"] not in known:
            raise ValueError("invalid or duplicate adapter method")
        parameters = set()
        for argument in method["arguments"]:
            keys(argument, {"name", "type", "ownership"}, {"name", "type", "ownership"})
            if (not isinstance(argument["name"], str) or argument["name"] in parameters or not FIELD.fullmatch(argument["name"]) or argument["name"] in RESERVED
                    or argument["type"] not in {*value["handles"], "Binding"} or argument["ownership"] not in {"borrow", "move"}):
                raise ValueError("invalid adapter argument")
            parameters.add(argument["name"])
        methods[name] = method
    if set(methods) != {"verify_installation", "verify_bootstrap", "authenticate_producer", "hold_spawn", "observe_held", "release", "inspect_caller", "caller_observation", "is_live", "cancel_hold", "terminate"}:
        raise ValueError("incomplete native caller adapter")
    release = methods["release"]
    if release["arguments"] != [{"name": "held", "type": "HeldProcess", "ownership": "move"}, {"name": "binding", "type": "Binding", "ownership": "borrow"}]:
        raise ValueError("release must consume its hold with a node binding")
    return value


def render(value: dict, adapter: dict) -> str:
    output = ['// Generated contract metadata. Transitions do not authorize operations.\nuse super::contract::ProcessObservation;\n',
              '#[derive(Clone, Copy, Debug, PartialEq, Eq)]\npub struct Transition { pub domain: &\'static str, pub from: &\'static str, pub event: &\'static str, pub to: &\'static str, pub checks: &\'static [&\'static str] }\n',
              'pub const TRANSITIONS: &[Transition] = &[']
    for domain in ("session", "invocation"):
        for row in value[domain]["transitions"]:
            checks = ", ".join('"' + check + '"' for check in value["requirements"][row["event"]])
            output.append(f'Transition {{ domain: "{domain}", from: "{row["from"]}", event: "{row["event"]}", to: "{row["to"]}", checks: &[{checks}] }},')
    output.append('];\n#[derive(Debug)]\npub enum NativeAdapterError { ' + ", ".join(adapter["errors"]) + ' }\n')
    output.append('/// Opaque runtime handles are never wire DTOs or proof supplied by a caller.\npub trait NativeCallerPlatform<Binding> {')
    output.extend('    type ' + name + ';' for name in adapter["handles"])
    for method in adapter["method"]:
        arguments = []
        for argument in method["arguments"]:
            kind = argument["type"] if argument["type"] == "Binding" else "Self::" + argument["type"]
            arguments.append(argument["name"] + ": " + ("&" if argument["ownership"] == "borrow" else "") + kind)
        result = method["result"]
        result = "()" if result == "unit" else result if result in {"bool", "ProcessObservation"} else "Self::" + result
        suffix = ", " + ", ".join(arguments) if arguments else ""
        output.append(f'    fn {method["name"]}(&mut self{suffix}) -> Result<{result}, NativeAdapterError>;')
    output.append('}\n')
    return "\n".join(output)

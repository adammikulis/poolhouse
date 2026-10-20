"""Typed workflow validation and reusable-group expansion."""
from __future__ import annotations

import itertools
import json
import math
import re
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from poolhouse.studio.catalog import node_spec

IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]+$")
MAX_NODES = 1000
MAX_DEPTH = 16


def _finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _finite(v) for k, v in value.items())
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    return value is None or isinstance(value, (str, int, bool))


def _parameters(values: Any, schema: dict) -> dict:
    if not isinstance(values, dict):
        raise ValueError("Parameters must be an object")
    fields = schema.get("properties", {})
    if set(values) - set(fields):
        raise ValueError(f"Unknown parameter: {sorted(set(values) - set(fields))[0]}")
    result = {}
    types = {"string": str, "number": (int, float), "integer": int, "boolean": bool}
    for key, field in fields.items():
        value = deepcopy(values.get(key, field.get("default")))
        if not isinstance(value, types[field["type"]]) or (field["type"] in ("number", "integer") and isinstance(value, bool)):
            raise ValueError(f"Invalid parameter type: {key}")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"Invalid choice: {key}")
        for bound, fails in (("minimum", lambda a, b: a < b), ("maximum", lambda a, b: a > b),
                             ("exclusiveMinimum", lambda a, b: a <= b), ("exclusiveMaximum", lambda a, b: a >= b)):
            if bound in field and fails(value, field[bound]):
                raise ValueError(f"Parameter outside range: {key}")
        result[key] = value
    return result


def _endpoint(value: Any) -> dict:
    if not isinstance(value, dict) or set(value) != {"node", "port"}:
        raise ValueError("Connection endpoints need a node and port")
    if not all(isinstance(value[k], str) and value[k] for k in value):
        raise ValueError("Connection endpoints need text identifiers")
    return dict(value)


def _group_interface(definition: dict, inner: dict, definitions: dict) -> dict:
    nodes = {node["id"]: node for node in inner["nodes"]}
    interfaces = {}
    for direction in ("inputs", "outputs"):
        exposed = definition.get(direction, {})
        if not isinstance(exposed, dict):
            raise ValueError("Group ports must be an object")
        interfaces[direction] = {}
        for name, raw in exposed.items():
            if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
                raise ValueError("Group port names must be simple names")
            endpoint = inner["resolve"](_endpoint(raw), direction)
            node = nodes[endpoint["node"]]
            ports = node_spec(node["type"], node.get("version", 1))[direction]
            if not any(port["name"] == endpoint["port"] for port in ports):
                raise ValueError("Group interface references an unknown port")
            interfaces[direction][name] = endpoint
    original = {node["id"]: node for node in definition["nodes"]}
    for name, target in definition.get("parameters", {}).items():
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or not isinstance(target, dict) or set(target) != {"node", "parameter"}:
            raise ValueError("Invalid exposed group parameter")
        node = original[target["node"]]
        fields = (definitions[node["group"]].get("parameters", {}) if node["type"] == "group"
                  else node_spec(node["type"], node.get("version", 1))["parameters"]["properties"])
        if target["parameter"] not in fields:
            raise ValueError("Group interface references an unknown parameter")
    return interfaces


def _execution(node: dict) -> None:
    if set(node) - {"id", "type", "version", "parameters", "bindings", "group", "name", "sweep", "execution"}:
        raise ValueError("Unknown node field")
    execution = node.get("execution", {})
    if (not isinstance(execution, dict) or set(execution) - {"machine"}
            or (execution and (not isinstance(execution.get("machine"), str)
                               or not execution["machine"] or len(execution["machine"]) > 256))):
        raise ValueError("Choose an authenticated device for execution")


def _expand(graph: dict, definitions: dict, stack: tuple = (), prefix: str = "") -> dict:
    if len(stack) > MAX_DEPTH:
        raise ValueError("Group nesting is too deep")
    nodes, edges, controls, instances = [], [], [], {}
    ids = set()
    for original in graph.get("nodes", []):
        if not isinstance(original, dict):
            raise ValueError("Nodes must be objects")
        node = deepcopy(original)
        identifier = node.get("id")
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in ids:
            raise ValueError("Node identifiers must be unique simple names")
        ids.add(identifier)
        _execution(node)
        if node.get("type") != "group":
            node["id"] = prefix + identifier
            nodes.append(node)
            continue
        key = node.get("group")
        if key not in definitions or key in stack:
            raise ValueError("Missing or recursive reusable group")
        definition = deepcopy(definitions[key])
        if not isinstance(definition, dict) or set(definition) - {"name", "nodes", "edges", "controls", "inputs", "outputs", "parameters"}:
            raise ValueError("Invalid reusable group definition")
        exposed = definition.get("parameters", {})
        if set(node.get("parameters", {})) - set(exposed):
            raise ValueError("Unknown exposed group parameter")
        for name, value in node.get("parameters", {}).items():
            target = exposed[name]
            found = next((n for n in definition.get("nodes", []) if n["id"] == target["node"]), None)
            if found is None:
                raise ValueError("Group parameter target is missing")
            found.setdefault("parameters", {})[target["parameter"]] = value
        child_prefix = prefix + identifier + "/"
        inner = _expand(definition, definitions, (*stack, key), child_prefix)
        nodes.extend(inner["nodes"])
        edges.extend(inner["edges"])
        controls.extend(inner["controls"])
        instances[identifier] = _group_interface(definition, inner, definitions)
        if len(nodes) > MAX_NODES:
            raise ValueError("Expanded workflow has too many nodes")

    def resolve(endpoint: dict, direction: str) -> dict:
        if endpoint["node"] not in ids:
            raise ValueError("Connection references a missing node")
        if endpoint["node"] in instances:
            try:
                return instances[endpoint["node"]][direction][endpoint["port"]]
            except KeyError as exc:
                raise ValueError("Connection references a missing group port") from exc
        return {"node": prefix + endpoint["node"], "port": endpoint["port"]}

    for edge in graph.get("edges", []):
        if not isinstance(edge, dict) or set(edge) != {"source", "target"}:
            raise ValueError("Connections need a source and target")
        edges.append({"source": resolve(_endpoint(edge["source"]), "outputs"),
                      "target": resolve(_endpoint(edge["target"]), "inputs")})
    controls.extend(_expand_controls(graph, resolve, instances, nodes, prefix))
    return {"nodes": nodes, "edges": edges, "controls": controls, "resolve": resolve}


def _expand_controls(graph: dict, resolve: Callable, instances: dict, nodes: list, prefix: str) -> list:
    if len(nodes) > MAX_NODES:
        raise ValueError("Expanded workflow has too many nodes")
    ids = {node["id"] for node in graph.get("nodes", [])}
    controls = []
    for control in graph.get("controls", []):
        if not isinstance(control, dict) or set(control) != {"source", "target"}:
            raise ValueError("Controls need a source gate and target node")
        target = control["target"]
        if not isinstance(target, dict) or set(target) != {"node"} or target["node"] not in ids:
            raise ValueError("Control target must be a node in this graph")
        source = resolve(_endpoint(control["source"]), "outputs")
        targets = ([n["id"] for n in nodes if n["id"].startswith(prefix + target["node"] + "/")]
                   if target["node"] in instances else [prefix + target["node"]])
        controls.extend({"source": source, "target": {"node": name}} for name in targets)
    return controls


def _validate_expanded(graph: dict, capabilities: set[str] | None, authorize: Callable | None) -> list[dict]:
    errors, specs, nodes = [], {}, {n["id"]: n for n in graph["nodes"]}
    def error(node: str, code: str, message: str) -> None:
        errors.append({"node": node, "code": code, "message": message})
    for identifier, node in nodes.items():
        try:
            spec = node_spec(node.get("type", ""), node.get("version", 1))
            specs[identifier] = spec
            node["version"] = spec["version"]
            node["parameters"] = _parameters(node.get("parameters", {}), spec["parameters"])
            if capabilities is not None and set(spec["capabilities"]) - capabilities:
                error(identifier, "dependency", "Required capabilities are unavailable")
            bindings = node.get("bindings", {})
            if not isinstance(bindings, dict):
                raise ValueError("Resource bindings must be an object")
            declared = spec.get("bindings", {})
            if set(bindings) - set(declared):
                raise ValueError("Unknown resource binding")
            for name, binding in bindings.items():
                if not isinstance(binding, dict) or set(binding) != {"kind", "id"} or binding["kind"] not in ("dataset", "model", "recordings", "traces") or not isinstance(binding["id"], str) or not binding["id"].strip() or binding["kind"] != declared[name]["kind"]:
                    raise ValueError("Invalid resource binding")
                if authorize is not None and not authorize(binding):
                    error(identifier, "unauthorized", "Resource access is unavailable")
            if any(field.get("required") and key not in bindings for key, field in declared.items()):
                error(identifier, "binding", "Select a resource for this node")
        except (ValueError, TypeError, KeyError) as exc:
            error(identifier, "node", str(exc))
    incoming, successors = {}, {key: set() for key in nodes}
    for edge in graph["edges"]:
        source, target = edge["source"], edge["target"]
        try:
            output = next(p for p in specs[source["node"]]["outputs"] if p["name"] == source["port"])
            port = next(p for p in specs[target["node"]]["inputs"] if p["name"] == target["port"])
            key = (target["node"], target["port"])
            if key in incoming:
                raise ValueError("An input has more than one connection")
            incoming[key] = source
            if output["type"] == "gate" or port["type"] == "gate":
                raise ValueError("Gate ports require control connections")
            if output["type"] not in port.get("accepted_types", [port["type"]]):
                raise ValueError("Connected port types do not match")
            if nodes[source["node"]].get("parameters", {}).get("mode") == "smoke" and source["port"] in ("model", "checkpoint"):
                raise ValueError("Smoke training does not produce a checkpoint or model")
            successors[source["node"]].add(target["node"])
        except (KeyError, StopIteration, ValueError) as exc:
            error(target["node"], "connection", str(exc) or "Unknown port")
    errors.extend(_validate_controls(graph, specs, successors))
    for identifier, spec in specs.items():
        for port in spec["inputs"]:
            if port.get("required") and (identifier, port["name"]) not in incoming:
                error(identifier, "input", f"Connect {port['name']}")
    if _has_cycle(nodes, successors):
        error("", "cycle", "Workflow connections contain a cycle")
    return errors


def _validate_controls(graph: dict, specs: dict, successors: dict) -> list:
    errors, seen_controls = [], set()
    for control in graph["controls"]:
        source, target = control["source"], control["target"]["node"]
        try:
            output = next(p for p in specs[source["node"]]["outputs"] if p["name"] == source["port"])
            if output["type"] != "gate" or target not in specs:
                raise ValueError("Controls must connect a declared gate to a known node")
            identity = (source["node"], source["port"], target)
            if identity in seen_controls:
                raise ValueError("Duplicate control connection")
            seen_controls.add(identity)
            successors[source["node"]].add(target)
        except (KeyError, StopIteration, ValueError) as exc:
            errors.append({"node": target, "code": "control", "message": str(exc) or "Unknown gate port"})
    return errors


def _has_cycle(nodes: dict, successors: dict) -> bool:
    pending = dict.fromkeys(nodes, 0)
    for children in successors.values():
        for child in children:
            pending[child] += 1
    ready = [node for node, count in pending.items() if not count]
    seen = 0
    while ready:
        node = ready.pop()
        seen += 1
        for child in successors[node]:
            pending[child] -= 1
            if not pending[child]:
                ready.append(child)
    return seen != len(nodes)


def _sweeps(graph: dict) -> dict:
    requests = []
    for node in graph["nodes"]:
        sweep = node.pop("sweep", {})
        if not isinstance(sweep, dict):
            raise ValueError("Sweep settings must map parameters to values")
        for parameter, values in sweep.items():
            if not isinstance(parameter, str) or not isinstance(values, list) or not values:
                raise ValueError("Each sweep parameter needs a nonempty value list")
            requests.append((node["id"], parameter, values))
    if not requests:
        return graph
    size = len(graph["nodes"])
    for _, _, values in requests:
        size *= len(values)
        if size > MAX_NODES:
            raise ValueError("Expanded sweep has too many nodes")
    nodes, edges, controls = [], [], []
    variants = {node["id"]: [] for node in graph["nodes"]}
    for index, values in enumerate(itertools.product(*(request[2] for request in requests))):
        instance = deepcopy(graph)
        by_id = {node["id"]: node for node in instance["nodes"]}
        for (identifier, parameter, _), value in zip(requests, values, strict=True):
            by_id[identifier].setdefault("parameters", {})[parameter] = value
        names = {identifier: f"{identifier}/sweep/{index}" for identifier in by_id}
        for node in instance["nodes"]:
            variants[node["id"]].append(names[node["id"]])
            node["id"] = names[node["id"]]
        for edge in [*instance["edges"], *instance["controls"]]:
            for endpoint in edge.values():
                endpoint["node"] = names[endpoint["node"]]
        nodes.extend(instance["nodes"])
        edges.extend(instance["edges"])
        controls.extend(instance["controls"])
    return {"nodes": nodes, "edges": edges, "controls": controls, "variants": variants}


def validate_graph(graph: Any, *, capabilities: set[str] | None = None,
                   authorize: Callable | None = None) -> dict:
    """Validate and expand a workflow without executing or resolving resources."""
    try:
        if not isinstance(graph, dict) or set(graph) - {"id", "name", "schema_version", "nodes", "edges", "controls", "groups", "layout", "description"}:
            raise ValueError("Invalid workflow fields")
        if type(graph.get("schema_version", 1)) is not int or graph.get("schema_version", 1) != 1 or not _finite(graph):
            raise ValueError("Unsupported workflow version or nonfinite value")
        if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges", []), list) or not isinstance(graph.get("controls", []), list) or not isinstance(graph.get("groups", {}), dict):
            raise ValueError("Workflow needs nodes, connections and group definitions")
        if len(graph["nodes"]) > MAX_NODES or len(json.dumps(graph)) > 2_000_000:
            raise ValueError("Workflow exceeds the supported graph size")
        definitions = graph.get("groups", {})
        for key in definitions:
            _expand({"nodes": [{"id": "check", "type": "group", "group": key}], "edges": []}, definitions)
        expanded = _expand(graph, definitions)
        expanded.pop("resolve")
        expanded = _sweeps(expanded)
        errors = _validate_expanded(expanded, capabilities, authorize)
        return {"valid": not errors, "errors": errors, "graph": expanded}
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        return {"valid": False, "errors": [{"node": "", "code": "graph", "message": str(exc)}], "graph": None}

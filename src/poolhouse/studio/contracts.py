"""Typed workflow validation and reusable-group expansion."""
from __future__ import annotations

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


def _expand(graph: dict, definitions: dict, stack: tuple = (), prefix: str = "") -> dict:
    if len(stack) > MAX_DEPTH:
        raise ValueError("Group nesting is too deep")
    nodes, edges, instances = [], [], {}
    ids = set()
    for original in graph.get("nodes", []):
        if not isinstance(original, dict):
            raise ValueError("Nodes must be objects")
        node = deepcopy(original)
        identifier = node.get("id")
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier) or identifier in ids:
            raise ValueError("Node identifiers must be unique simple names")
        ids.add(identifier)
        if set(node) - {"id", "type", "version", "parameters", "bindings", "group", "name"}:
            raise ValueError("Unknown node field")
        if node.get("type") != "group":
            node["id"] = prefix + identifier
            nodes.append(node)
            continue
        key = node.get("group")
        if key not in definitions or key in stack:
            raise ValueError("Missing or recursive reusable group")
        definition = deepcopy(definitions[key])
        if not isinstance(definition, dict) or set(definition) - {"name", "nodes", "edges", "inputs", "outputs", "parameters"}:
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
        instances[identifier] = {direction: {name: inner["resolve"](_endpoint(endpoint), direction)
                                            for name, endpoint in definition.get(direction, {}).items()}
                                 for direction in ("inputs", "outputs")}
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
    if len(nodes) > MAX_NODES:
        raise ValueError("Expanded workflow has too many nodes")
    return {"nodes": nodes, "edges": edges, "resolve": resolve}


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
            if output["type"] != port["type"]:
                raise ValueError("Connected port types do not match")
            if nodes[source["node"]].get("parameters", {}).get("mode") == "smoke" and source["port"] in ("model", "checkpoint"):
                raise ValueError("Smoke training does not produce a checkpoint or model")
            successors[source["node"]].add(target["node"])
        except (KeyError, StopIteration, ValueError) as exc:
            error(target["node"], "connection", str(exc) or "Unknown port")
    for identifier, spec in specs.items():
        for port in spec["inputs"]:
            if port.get("required") and (identifier, port["name"]) not in incoming:
                error(identifier, "input", f"Connect {port['name']}")
    if _has_cycle(nodes, successors):
        error("", "cycle", "Workflow connections contain a cycle")
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


def validate_graph(graph: Any, *, capabilities: set[str] | None = None,
                   authorize: Callable | None = None) -> dict:
    """Validate and expand a workflow without executing or resolving resources."""
    try:
        if not isinstance(graph, dict) or set(graph) - {"id", "name", "schema_version", "nodes", "edges", "groups", "layout", "description"}:
            raise ValueError("Invalid workflow fields")
        if type(graph.get("schema_version", 1)) is not int or graph.get("schema_version", 1) != 1 or not _finite(graph):
            raise ValueError("Unsupported workflow version or nonfinite value")
        if not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges", []), list) or not isinstance(graph.get("groups", {}), dict):
            raise ValueError("Workflow needs nodes, connections and group definitions")
        if len(graph["nodes"]) > MAX_NODES or len(json.dumps(graph)) > 2_000_000:
            raise ValueError("Workflow exceeds the supported graph size")
        expanded = _expand(graph, graph.get("groups", {}))
        expanded.pop("resolve")
        errors = _validate_expanded(expanded, capabilities, authorize)
        return {"valid": not errors, "errors": errors, "graph": expanded}
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        return {"valid": False, "errors": [{"node": "", "code": "graph", "message": str(exc)}], "graph": None}

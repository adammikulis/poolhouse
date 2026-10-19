"""Inert, immutable portable workflow packages."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from poolhouse.studio.catalog import node_spec
from poolhouse.studio.contracts import _finite, validate_graph

PACKAGE_FIELDS = {"schema_version", "id", "version", "kind", "name", "description", "licence",
                  "tags", "provenance", "dependencies", "graph", "digest", "portable_parameters"}
HEX = re.compile(r"^[0-9a-f]{64}$")
ID = re.compile(r"^[a-zA-Z0-9_.-]+$")


def content_digest(content: Any) -> str:
    """Return a canonical SHA-256 digest of JSON content."""
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _interfaces(value: dict, fields: set[str]) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Invalid group interface")
    result = {}
    for key, endpoint in value.items():
        if not isinstance(key, str) or not ID.fullmatch(key) or not isinstance(endpoint, dict) or set(endpoint) != fields:
            raise ValueError("Invalid group interface fields")
        if any(not isinstance(item, str) or not ID.fullmatch(item) for item in endpoint.values()):
            raise ValueError("Invalid group interface target")
        result[key] = dict(endpoint)
    return result


def _edge(edge: dict) -> dict:
    if not isinstance(edge, dict) or set(edge) != {"source", "target"}:
        raise ValueError("Invalid connection fields")
    return _interfaces(edge, {"node", "port"})


def _fields(node: dict, definitions: dict, stack: tuple = ()) -> dict:
    if node.get("type") != "group":
        return node_spec(node["type"], node.get("version", 1))["parameters"]["properties"]
    key = node.get("group")
    if key in stack or key not in definitions:
        raise ValueError("Missing or recursive reusable group")
    group = definitions[key]
    targets = _interfaces(group.get("parameters", {}), {"node", "parameter"})
    nodes = {inner["id"]: inner for inner in group["nodes"]}
    return {name: _fields(nodes[target["node"]], definitions, (*stack, key))[target["parameter"]]
            for name, target in targets.items()}


def _portable_node(node: dict, selected: dict, definitions: dict, prefix: str = "") -> dict:
    fields = _fields(node, definitions)
    portable = {key: value for key, value in node.get("parameters", {}).items()
                if key in fields and (fields[key]["type"] in ("number", "integer", "boolean")
                                      or value in fields[key].get("enum", [])
                                      or key in selected.get(prefix + node["id"], []))}
    result = {"id": node["id"], "type": node["type"], "parameters": portable}
    if node.get("type") == "group":
        result["group"] = node["group"]
    else:
        result.update(version=node.get("version", 1), bindings={})
    if "sweep" in node:
        result["sweep"] = {key: values for key, values in node["sweep"].items()
                           if key in fields and all(isinstance(value, (int, float, bool)) or value in fields[key].get("enum", [])
                                                    or key in selected.get(prefix + node["id"], []) for value in values)}
    return result


def _portable_graph(graph: dict, selected: dict | None = None, prefix: str = "", definitions: dict | None = None) -> dict:
    selected = selected or {}
    definitions = definitions if definitions is not None else graph.get("groups", {})
    groups = {}
    for key, group in graph.get("groups", {}).items():
        if set(group) - {"name", "nodes", "edges", "inputs", "outputs", "parameters"}:
            raise ValueError("Invalid group definition fields")
        if not isinstance(group.get("name", key), str):
            raise ValueError("Group name must be text")
        nested = _portable_graph(group, selected, prefix + key + "/", definitions)
        groups[key] = {"name": group.get("name", key), "nodes": nested["nodes"], "edges": nested["edges"],
                       "inputs": _interfaces(group.get("inputs", {}), {"node", "port"}),
                       "outputs": _interfaces(group.get("outputs", {}), {"node", "port"}),
                       "parameters": _interfaces(group.get("parameters", {}), {"node", "parameter"})}
    return {"nodes": [_portable_node(node, selected, definitions, prefix) for node in graph["nodes"]],
            "edges": [_edge(edge) for edge in graph.get("edges", [])], "groups": groups}


def _declarations(graph: dict, selected: dict) -> None:
    if not isinstance(selected, dict):
        raise ValueError("Invalid portable parameter declaration")
    known = {node["id"]: _fields(node, graph.get("groups", {})) for node in graph["nodes"]}
    for key, group in graph.get("groups", {}).items():
        known.update({key + "/" + node["id"]: _fields(node, graph["groups"]) for node in group["nodes"]})
    for identifier, fields in selected.items():
        if identifier not in known or not isinstance(fields, list) or len(fields) != len(set(fields)):
            raise ValueError("Unknown portable parameter declaration")
        if any(not isinstance(field, str) or field not in known[identifier] for field in fields):
            raise ValueError("Unknown portable parameter field")


def _dependencies(graph: dict) -> list[dict]:
    found = {}
    def visit(value: dict) -> None:
        for node in value["nodes"]:
            if node["type"] != "group":
                spec = node_spec(node["type"], node.get("version", 1))
                found[spec["type"]] = {"id": spec["type"], "version": spec["version"], "digest": content_digest(spec)}
        for group in value.get("groups", {}).values():
            visit(group)
    visit(graph)
    return [found[key] for key in sorted(found)]


def export_package(graph: dict, *, package_id: str, version: str, name: str,
                   **metadata: Any) -> dict:
    """Export declared graph structure and portable numeric or enumerated parameters."""
    selected = metadata.get("portable_parameters", {})
    _declarations(graph, selected)
    portable = _portable_graph(graph, selected)
    package = {"schema_version": 1, "id": package_id, "version": version, "kind": metadata.get("kind", "workflow"),
               "name": name, "description": metadata.get("description", ""), "licence": metadata.get("licence", ""),
               "tags": metadata.get("tags", []),
               "provenance": {}, "portable_parameters": selected, "dependencies": _dependencies(portable), "graph": portable}
    package["digest"] = content_digest(package)
    return import_package(package)


def import_package(value: Any) -> dict:
    """Verify portable package content without executing, installing or fetching anything."""
    if not isinstance(value, dict) or set(value) != PACKAGE_FIELDS or not _finite(value):
        raise ValueError("Invalid package fields")
    package = deepcopy(value)
    if package["schema_version"] != 1 or package["kind"] not in ("workflow", "group", "node"):
        raise ValueError("Unsupported package version or kind")
    for key in ("id", "version"):
        if not isinstance(package[key], str) or not ID.fullmatch(package[key]):
            raise ValueError("Package identity and version must be simple names")
    for key in ("name", "description", "licence"):
        if not isinstance(package[key], str):
            raise ValueError("Package documentation must be text")
    if not isinstance(package["tags"], list) or any(not isinstance(tag, str) for tag in package["tags"]):
        raise ValueError("Package tags must be text")
    if not isinstance(package["provenance"], dict) or set(package["provenance"]) - {"source_id", "source_version", "source_digest"}:
        raise ValueError("Package provenance must not carry runtime identity")
    for key, item in package["provenance"].items():
        pattern = HEX if key == "source_digest" else ID
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise ValueError("Invalid package provenance value")
    digest = package.pop("digest")
    if not isinstance(digest, str) or not HEX.fullmatch(digest) or content_digest(package) != digest:
        raise ValueError("Package content digest does not match")
    package["digest"] = digest
    graph = package["graph"]
    if not isinstance(graph, dict) or set(graph) != {"nodes", "edges", "groups"}:
        raise ValueError("Package graph contains undeclared content")
    if len(json.dumps(graph)) > 2_000_000:
        raise ValueError("Package graph is too large")
    try:
        selected = package["portable_parameters"]
        if not isinstance(selected, dict) or any(not isinstance(k, str) or not isinstance(v, list) or any(not isinstance(f, str) for f in v) for k, v in selected.items()):
            raise ValueError("Invalid portable parameter declaration")
        _declarations(graph, selected)
        sanitized = _portable_graph(graph, selected)
    except (KeyError, TypeError, RecursionError) as exc:
        raise ValueError("Invalid package graph") from exc
    if graph != sanitized:
        raise ValueError("Package contains private or undeclared node content")
    errors = validate_graph(graph)["errors"]
    allowed_errors = {"binding"} | ({"input"} if package["kind"] in ("node", "group") else set())
    if any(error["code"] not in allowed_errors for error in errors):
        raise ValueError("Package graph is invalid: " + "; ".join(error["message"] for error in errors))
    if package["dependencies"] != _dependencies(graph):
        raise ValueError("Pinned package dependencies are missing or do not match")
    return package

"""Typed operation declarations; these generate no dispatch or authorization."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from .common import GENERATED_TYPES, IDENTIFIER, keys
from .lifecycle import CHECK


def load_operations(root: Path, rows: list[dict]) -> list[dict]:
    source = tomllib.loads((root / "operations.toml").read_text())
    keys(source, {"version", "status", "authorization", "operation"}, {"version", "status", "authorization", "operation"})
    if (type(source["version"]) is not int or source["version"] != 1 or source["status"] != "proposal"
            or source["authorization"] != "node-checked-code" or not isinstance(source["operation"], list)):
        raise ValueError("unsupported operation contract")
    definitions = {row["name"]: row for row in rows}
    names, symbols = set(), set(definitions) | GENERATED_TYPES | {"Operation", "OperationSpec", "OPERATION_TYPES"}
    fields = {"name", "symbol", "request", "response", "error", "authorization", "retry", "cancellation", "subscription"}
    for operation in source["operation"]:
        keys(operation, fields, fields)
        name, symbol = operation["name"], operation["symbol"]
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+", name) or name in names
                or not isinstance(symbol, str) or not IDENTIFIER.fullmatch(symbol) or symbol in symbols):
            raise ValueError("invalid or duplicate operation name")
        names.add(name)
        symbols.add(symbol)
        for field in ("request", "response", "error"):
            reference = operation[field]
            if not isinstance(reference, str) or reference not in definitions or definitions[reference]["visibility"] != "public":
                raise ValueError("operation references missing or private data")
        if definitions[operation["request"]]["kind"] != "record":
            raise ValueError("operation requests must be closed records")
        checks = operation["authorization"]
        if (not isinstance(checks, list) or not checks or any(not isinstance(check, str) or not CHECK.fullmatch(check) for check in checks)
                or len(checks) != len(set(checks)) or "authenticated-current-principal" not in checks):
            raise ValueError("invalid operation authorization requirements")
        if (operation["retry"] not in {"read-current-state", "durable-principal-resource-generation-payload-bound"}
                or operation["cancellation"] not in {"request-detach-only", "atomic-intent-versus-launch-and-completion"}
                or operation["subscription"] != "none"):
            raise ValueError("unsupported operation lifecycle")
        if operation["retry"] == "durable-principal-resource-generation-payload-bound":
            members = {field["name"]: field["type"] for field in definitions[operation["request"]]["fields"]}
            if members.get("request_id") != "RequestId" or members.get("generation") != "ResourceGeneration":
                raise ValueError("durable operation omits request or generation binding")
    if not names:
        raise ValueError("empty operation contract")
    return source["operation"]


def render_rust(rows: list[dict], digest: str) -> str:
    output = [f'// Generated proposal. No dispatch or authority is implemented. Schema {digest}\nuse super::contract::*;\n',
              'pub trait Operation { type Request; type Response; type Error; const NAME: &\'static str; }\n']
    for row in rows:
        output.append(f'pub struct {row["symbol"]};\nimpl Operation for {row["symbol"]} {{ type Request = {row["request"]}; type Response = {row["response"]}; type Error = {row["error"]}; const NAME: &\'static str = "{row["name"]}"; }}\n')
    return "\n".join(output)


def render_python(rows: list[dict], digest: str) -> str:
    output = [f'# Generated proposal. No dispatch or authority is implemented. Schema {digest}\nfrom . import node_contract as data\n\nOPERATION_TYPES = {{']
    for row in rows:
        output.append(f'    "{row["name"]}": (data.{row["request"]}, data.{row["response"]}, data.{row["error"]}),')
    output.append('}\n')
    return "\n".join(output)


def render_typescript(rows: list[dict], digest: str) -> str:
    names = sorted({row[field] for row in rows for field in ("request", "response", "error")})
    output = [f'// Generated proposal. No dispatch or authority is implemented. Schema {digest}\nimport type {{ ' + ", ".join(names) + ' } from "./node";\nexport interface NodeOperations {']
    for row in rows:
        output.append(f'  readonly {json.dumps(row["name"])}: {{ readonly request: {row["request"]}; readonly response: {row["response"]}; readonly error: {row["error"]} }};')
    output.append('}\n')
    return "\n".join(output)

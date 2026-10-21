"""File locking outside the lock module."""

from __future__ import annotations

import ast
from pathlib import Path

from . import Finding
from ._perfile import finder
from ._util import calls, dotted, exempt, parse

NAME = "file-locks"
OWNER = "poolhouse.lock"
INCREMENTAL = True
ROOTS = ("src/poolhouse",)
OWNS = ("src/poolhouse/lock.py",)
MODULES = {"fcntl", "msvcrt"}


def describe() -> str:
    return "A lock taken on a file by hand; poolhouse.lock holds one across both platforms."


def _handle_transfer_only(tree: ast.Module, binding: str) -> bool:
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    references = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Name) and node.id == binding]
    return bool(references) and all(
        isinstance(parents.get(node), ast.Attribute)
        and parents[node].value is node
        and parents[node].attr == "open_osfhandle"
        and isinstance(parents[node].ctx, ast.Load)
        for node in references)



def _unmodified_module(tree: ast.Module, module: str, binding: str) -> bool:
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names if (alias.asname or alias.name.split(".")[0]) == binding)
        elif isinstance(node, ast.ImportFrom):
            if any((alias.asname or alias.name) == binding for alias in node.names):
                return False
        elif ((isinstance(node, ast.Name) and node.id == binding and not isinstance(node.ctx, ast.Load))
              or (isinstance(node, ast.arg) and node.arg == binding)
              or (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.ExceptHandler,
                                    ast.MatchAs, ast.MatchStar)) and node.name == binding)
              or (isinstance(node, ast.MatchMapping) and node.rest == binding)
              or (isinstance(node, (ast.Global, ast.Nonlocal)) and binding in node.names)):
            return False
    return imports == [module]



def _terminal_only(tree: ast.Module, parents: dict, terminal: str, binding: str) -> bool:
    if not _unmodified_module(tree, "termios", terminal):
        return False
    for reference in ast.walk(tree):
        if not isinstance(reference, ast.Name) or reference.id != terminal:
            continue
        attribute = parents.get(reference)
        call = parents.get(attribute)
        if not (isinstance(attribute, ast.Attribute) and attribute.value is reference
                and attribute.attr == "FIONREAD" and isinstance(attribute.ctx, ast.Load)
                and isinstance(call, ast.Call) and len(call.args) == 4 and call.args[1] is attribute
                and isinstance(call.func, ast.Attribute) and call.func.attr == "ioctl"
                and isinstance(call.func.value, ast.Name) and call.func.value.id == binding):
            return False
    return True

def _inspection_only(tree: ast.Module, binding: str) -> bool:
    if not _unmodified_module(tree, "fcntl", binding):
        return False
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    references = [node for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id == binding]
    for reference in references:
        attribute = parents.get(reference)
        if not (isinstance(attribute, ast.Attribute) and attribute.value is reference
                and isinstance(attribute.ctx, ast.Load)):
            return False
        if attribute.attr in ("F_GETFL", "F_GETFD", "FD_CLOEXEC"):
            continue
        call = parents.get(attribute)
        if not isinstance(call, ast.Call) or call.func is not attribute or call.keywords:
            return False
        if attribute.attr == "fcntl" and len(call.args) == 2:
            operation = call.args[1]
            if (isinstance(operation, ast.Attribute) and isinstance(operation.value, ast.Name)
                    and operation.value.id == binding and operation.attr in ("F_GETFL", "F_GETFD")):
                continue
        if attribute.attr == "ioctl" and len(call.args) == 4 and isinstance(call.args[3], ast.Constant) and call.args[3].value is True:
            operation = call.args[1]
            if isinstance(operation, ast.Attribute) and operation.attr == "FIONREAD" and isinstance(operation.value, ast.Name):
                terminal = operation.value.id
                if _terminal_only(tree, parents, terminal, binding):
                    continue
        return False
    return bool(references)

def scan(path: Path, where: str) -> list[Finding]:
    out = []
    if exempt(where, OWNS):
        return out
    tree = parse(path)
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (alias.name.split(".")[0] in MODULES
                        and not ((alias.name == "msvcrt"
                                  and _handle_transfer_only(tree, alias.asname or alias.name))
                                 or (alias.name == "fcntl" and _inspection_only(tree, alias.asname or alias.name)))):
                    out.append(Finding(where, node.lineno, f"import {alias.name}"))
        elif (isinstance(node, ast.ImportFrom) and node.module
              and node.module.split(".")[0] in MODULES
              and (node.module != "msvcrt" or any(
                  alias.name != "open_osfhandle" for alias in node.names))):
            out.append(Finding(where, node.lineno, f"from {node.module}"))
    for node, name in calls(tree):
        if name.endswith("flock") or dotted(node.func).endswith("flock"):
            out.append(Finding(where, node.lineno, "flock"))
        elif name == "msvcrt.locking":
            out.append(Finding(where, node.lineno, "msvcrt.locking"))
    return out


find = finder(ROOTS, scan)

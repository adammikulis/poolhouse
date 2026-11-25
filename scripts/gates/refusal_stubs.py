"""A function that takes nothing and only raises a fixed message: a feature switched off for good."""

from __future__ import annotations

import ast
from pathlib import Path

from . import Finding
from ._perfile import finder
from ._util import parse

NAME = "refusal-stubs"
OWNER = ""
INCREMENTAL = True
ROOTS = ("src/poolhouse",)
ABSTRACT = ("abstractmethod", "overload")


def describe() -> str:
    return ("A function with no parameters whose whole body is `raise` of a fixed message refuses "
            "every caller for ever; build the feature or delete its entry points.")


def _body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _fixed(raised: ast.expr | None) -> bool:
    """The raise names an exception built from literals only, so no argument can change it."""
    if not isinstance(raised, ast.Call):
        return False
    if isinstance(raised.func, ast.Name) and raised.func.id == "NotImplementedError":
        return False
    return all(isinstance(a, ast.Constant) for a in raised.args) and not raised.keywords


def _takes_nothing(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = node.args
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    if names and names[0] in ("self", "cls"):
        names = names[1:]
    return not names and not args.vararg and not args.kwarg


def _marked(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Abstract, overloaded, or a dunder: `__new__`, `__init__` and `__reduce__` that refuse
    construction or pickling are guards on a type, not a feature switched off."""
    if node.name.startswith("__") and node.name.endswith("__"):
        return True
    for deco in node.decorator_list:
        name = deco.attr if isinstance(deco, ast.Attribute) else getattr(deco, "id", "")
        if name in ABSTRACT:
            return True
    return False


def scan(path: Path, where: str) -> list[Finding]:
    out = []
    tree = parse(path)
    if tree is None:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or _marked(node):
            continue
        body = _body(node)
        if len(body) == 1 and isinstance(body[0], ast.Raise) and _fixed(body[0].exc) \
                and _takes_nothing(node):
            out.append(Finding(where, node.lineno, f"{node.name}: raises a fixed message"))
    return out


find = finder(ROOTS, scan)

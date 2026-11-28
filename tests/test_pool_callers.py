"""Every call of `poolhouse.api.pool` in the code and in the examples a reader runs names a function it has, with arguments that function takes.

The page's routes and the `poolhouse-pool` command reach the module through names no type checker follows (`ui.pool_actions`, handed in by the
command that starts the daemon), so a changed signature broke them without a word: each use is read from the source and bound to the signature.
"""

from __future__ import annotations

import ast
import inspect
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from poolhouse.api import pool

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ("docs/api.md",)
SOURCES = sorted(path for top in ("src", "tests", "scripts") for path in (ROOT / top).rglob("*.py")
                 if "api.pool" in (text := path.read_text(encoding="utf-8")) or "pool_actions" in text or "ph.pool" in text
                 or re.search(r"from (poolhouse\.api|\.+api) import [^\n]*\bpool\b", text))


def _named(node: ast.AST) -> str:
    """``a.b.c`` for a chain of names, '' for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and (base := _named(node.value)):
        return f"{base}.{node.attr}"
    return ""


def _names_of_the_module(tree: ast.Module) -> set[str]:
    """What the module is called in ``tree``."""
    called = {"ph.pool", "poolhouse.pool", "self.ui.pool_actions", "pool_cli.pool"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("api"):
            called |= {alias.asname or alias.name for alias in node.names if alias.name == "pool"}
    return called


def wrong(source: str) -> Iterator[str]:
    """What ``source`` asks of the module that it does not have."""
    tree = ast.parse(source)
    called = _names_of_the_module(tree)
    calls = {id(node.func): node for node in ast.walk(tree) if isinstance(node, ast.Call)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or _named(node.value) not in called:
            continue
        if not hasattr(pool, node.attr):
            yield f"line {node.lineno}: there is no pool.{node.attr}"
            continue
        call = calls.get(id(node))
        if call is None or any(isinstance(a, ast.Starred) for a in call.args) or any(k.arg is None for k in call.keywords):
            continue
        try:
            inspect.signature(getattr(pool, node.attr)).bind(*call.args, **{k.arg: k.value for k in call.keywords})
        except TypeError as why:
            yield f"line {node.lineno}: pool.{node.attr}: {why}"


@pytest.mark.parametrize("path", SOURCES, ids=lambda path: path.relative_to(ROOT).as_posix())
def test_the_code_calls_the_pool_api_as_it_is(path):
    assert not list(wrong(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", DOCUMENTS)
def test_the_examples_call_the_pool_api_as_it_is(name):
    examples = re.findall(r"^```python\n(.*?)^```", (ROOT / name).read_text(encoding="utf-8"), re.MULTILINE | re.DOTALL)
    assert any("ph.pool." in example for example in examples), f"{name} has no pool example to check"
    assert not [said for example in examples if "ph.pool." in example for said in wrong(example)]


def test_a_call_the_module_no_longer_takes_is_found():
    source = "from poolhouse.api import pool\npool.found('home', say=print)\nself.ui.pool_actions.sync_everything()\n"
    assert list(wrong(source)) == ["line 2: pool.found: got an unexpected keyword argument 'say'",
                                   "line 3: there is no pool.sync_everything"]

"""A string that tells somebody to run a package installer: what Poolhouse should have installed itself."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from . import Finding
from ._perfile import finder
from ._util import parse

NAME = "install-instructions"
OWNER = "poolhouse.provision"
INCREMENTAL = True
ROOTS = ("src/poolhouse",)
DATA = ("src/poolhouse/decide/guards/", "src/poolhouse/testing/injection_corpus.py", "src/poolhouse/guard/execchain.py")
"""Where an installer command is what is being judged or matched, never said to anybody."""
COMMAND = re.compile(
    r"\b(pip3? install|uv pip install|uv add |uv tool install|brew install|apt(-get)? install|winget install|choco install|"
    r"scoop install|npm install |playwright install|spacy download)|\binstall (poolhouse\[|@[\w-]+/)")
MANAGERS = ("pip", "pip3", "brew", "apt", "apt-get", "winget", "choco", "scoop", "npm")


def describe() -> str:
    return ("A string telling somebody to run a package installer; install it at first use (`poolhouse.provision` and "
            "the installers beside it), or say why only the account holder can.")


def notes(root: Path) -> list[str]:
    return ["install-instructions counts installer commands written out in strings under src/poolhouse; an instruction "
            "worded without one, and anything in scripts, installers or app pages, is not counted"]


def _documentation(tree: ast.AST) -> set[int]:
    """The strings standing alone as statements (docstrings, of a module, a function or a field): said to nobody."""
    return {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}


def _spelled(node: ast.List | ast.Tuple) -> bool:
    """A command written as its words: ``["pip", "install", ...]``."""
    words = [item.value if isinstance(item, ast.Constant) else None for item in node.elts[:2]]
    return len(words) == 2 and words[0] in MANAGERS and words[1] == "install"


def scan(path: Path, where: str) -> list[Finding]:
    if where.startswith(DATA):
        return []
    tree = parse(path)
    if tree is None:
        return []
    documentation = _documentation(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in documentation:
            if found := COMMAND.search(node.value):
                out.append(Finding(where, node.lineno, found.group(0).strip()))
        elif isinstance(node, (ast.List, ast.Tuple)) and _spelled(node):
            out.append(Finding(where, node.lineno, f"{node.elts[0].value} install, as words"))
    return out


find = finder(ROOTS, scan)

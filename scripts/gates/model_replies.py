"""A model-shaped reply built in the suite instead of served, marked, by poolhouse.testing.canned."""

from __future__ import annotations

import ast
from pathlib import Path

from . import Finding
from ._perfile import each_file
from ._util import parse, python_files

NAME = "model-replies"
OWNER = "poolhouse.testing.canned"
INCREMENTAL = True
HARD = True
ROOTS = ("tests",)
KNOWN_FILE = "scripts/gates/model_replies.txt"
"""``path count`` lines: the files that still build this many replies by hand, until their batch moves them."""
REPLY_KEYS = frozenset({"top_logprobs", "logprobs", "relevance_score", "delta"})
CHOICE_KEYS = frozenset({"message", "delta", "text", "finish_reason", "logprobs", "index"})
REPLY_TEXT = ('"choices"', "'choices'", '"top_logprobs"', "'top_logprobs'")


def describe() -> str:
    return ("A chat completion, logprob, rerank or streamed delta built in the suite; "
            "poolhouse.testing.canned serves them, every one marked as not a model.")


def _keys(node: ast.Dict) -> dict[str, ast.expr]:
    return {k.value: v for k, v in zip(node.keys, node.values, strict=True)
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _reply(node: ast.AST) -> str:
    """What reply a node builds, or empty: a mapping with the keys of one, or text holding them."""
    if isinstance(node, ast.Dict):
        keys = _keys(node)
        for name in sorted(REPLY_KEYS & keys.keys()):
            return f'"{name}" reply'
        rows = keys.get("choices")
        if isinstance(rows, ast.List) and any(
                isinstance(row, ast.Dict) and CHOICE_KEYS & _keys(row).keys() for row in rows.elts):
            return '"choices" reply'
    elif isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
        text = node.value if isinstance(node.value, str) else node.value.decode("utf-8", "replace")
        if any(mark in text for mark in REPLY_TEXT):
            return "reply text"
    return ""


def scan(path: Path, where: str) -> list[Finding]:
    """Every site in the file that builds a model-shaped reply."""
    tree = parse(path)
    if tree is None:
        return []
    return [Finding(where, node.lineno, what) for node in ast.walk(tree)
            if (what := _reply(node)) and hasattr(node, "lineno")]


def known(root: Path) -> dict[str, int]:
    """The files `KNOWN_FILE` lists and how many replies each may still build."""
    try:
        lines = (root / KNOWN_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    rows = [line.split() for line in lines if line.strip() and not line.startswith("#")]
    return {name: int(count) for name, count in rows}


def over(sites: list[Finding], allowed: dict[str, int]) -> list[Finding]:
    """The sites beyond what each file may build, and the files that build fewer than their line says."""
    held: dict[str, list[Finding]] = {}
    for site in sites:
        held.setdefault(site.path, []).append(site)
    out = [Finding(site.path, site.line, f"new: {site.detail}")
           for name, rows in held.items() for site in rows[allowed.get(name, 0):]]
    out += [Finding(name, 0, f"{KNOWN_FILE} lists {count}, the file builds {len(held.get(name, []))}: lower it")
            for name, count in allowed.items() if len(held.get(name, [])) < count]
    return sorted(out, key=lambda f: (f.path, f.line))


def find(root: Path) -> list[Finding]:
    return over(each_file(root, python_files(root, ROOTS), scan), known(root))

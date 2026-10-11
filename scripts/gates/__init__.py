"""Checkers that count the shapes this repository has a budget for."""

from __future__ import annotations

import importlib
import pkgutil
import shutil
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import ModuleType

from ._util import DEFAULT_INPUTS, remembered, tree_fingerprint


@dataclass(frozen=True)
class Finding:
    """One site a checker objects to."""

    path: str
    line: int
    detail: str = ""


def checkers() -> list[ModuleType]:
    """Every checker module in this package, by name."""
    found = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        for attr in ("NAME", "OWNER", "describe", "find"):
            if not hasattr(module, attr):
                raise AttributeError(f"gates.{info.name} has no {attr}")
        if not getattr(module, "INCREMENTAL", False) and not getattr(module, "INPUTS", ()):
            raise AttributeError(f"gates.{info.name} is remembered by tree and names no INPUTS")
        found.append(module)
    return found


def hard() -> set[str]:
    """The metrics with no budget, where one finding fails."""
    return {c.NAME for c in checkers() if getattr(c, "HARD", False)}


def skipped(checker: ModuleType) -> str:
    """Why this checker cannot run here, empty when it can."""
    reason = getattr(checker, "skip", None)
    return reason() if reason is not None else ""


def notes(root: Path) -> list[str]:
    """Every checker's lines about what its count does not cover."""
    out: list[str] = []
    for checker in checkers():
        said = getattr(checker, "notes", None)
        if said is not None and not skipped(checker):
            out.extend(said(root))
    return out


def unrunnable() -> dict[str, str]:
    """Metric name -> why it cannot be counted here."""
    return {c.NAME: r for c in checkers() if (r := skipped(c))}


def findings(checker: ModuleType, root: Path) -> list[Finding]:
    """One checker's findings over the tree at root, in path order.

    The tree this package sits in is remembered by the content of the files the checker names
    in ``INPUTS``, with the version of each external tool it may call, so a tree unchanged
    where the checker reads is not scanned twice. A checker marked ``INCREMENTAL`` keeps its
    own per-file results and is never held whole.
    """
    def scan() -> list[list]:
        found = sorted(checker.find(root), key=lambda f: (f.path, f.line, f.detail))
        return [[f.path, f.line, f.detail] for f in found]

    if root.resolve() != Path(__file__).resolve().parents[2] or getattr(checker, "INCREMENTAL", False):
        return [Finding(*row) for row in scan()]
    tools = ",".join(f"{t}={_stamp(t)}" for t in ("ruff", "pyright"))
    return [Finding(*row) for row in remembered(root, checker.NAME, scan, tools,
                                                 repo_fingerprint(tuple(checker.INPUTS)))]


@cache
def repo_fingerprint(inputs: tuple[str, ...] = DEFAULT_INPUTS) -> str:
    """The fingerprint of what the tree this package sits in holds under ``inputs``, taken once per process."""
    return tree_fingerprint(Path(__file__).resolve().parents[2], inputs)


def _stamp(tool: str) -> str:
    """Where a tool is and when it was installed, empty when it is absent."""
    path = shutil.which(tool)
    return "" if path is None else f"{path}@{Path(path).resolve().stat().st_mtime_ns}"


def run(root: Path) -> dict[str, list[Finding]]:
    """Every runnable checker's findings over the tree at root, keyed by metric name."""
    return {c.NAME: findings(c, root) for c in checkers() if not skipped(c)}

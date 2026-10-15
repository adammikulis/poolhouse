"""Lazy command declarations for umbrella routing and help."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path

from poolhouse.cli.reference import HELP

PREFIX = "poolhouse-"
RUNTIME = "poolhouse.runtime_cli:main"


@dataclass(frozen=True)
class Command:
    """A declared command target and its help presentation."""

    target: str
    summary: str
    public: bool = True


def registry(pyproject: Path) -> dict[str, Command]:
    """Read this package's declarations without importing command handlers."""
    declarations: Iterable[tuple[str, str]]
    if pyproject.exists():
        table = tomllib.loads(pyproject.read_text()).get("project", {}).get("scripts", {})
        declarations = table.items()
    else:
        declarations = ((point.name, point.value) for point in distribution("poolhouse").entry_points
                        if point.group == "console_scripts")
    found: dict[str, Command] = {}
    for name, target in declarations:
        if not name.startswith(PREFIX):
            continue
        word = name[len(PREFIX):]
        if not word or word in found or word == "runtime":
            raise ValueError(f"duplicate or reserved Poolhouse command: {name}")
        found[word] = Command(target, HELP.get(word, "Installed command; use its --help for details."),
                              public=word != "traind")
    found["runtime"] = Command(RUNTIME, HELP["runtime"])
    return found

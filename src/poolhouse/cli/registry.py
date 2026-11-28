"""Lazy command declarations for umbrella routing and help.

A command is a word. ``poolhouse <word>`` runs it, always. It is also installed as the console
script ``poolhouse-<word>`` only when something invokes it by that name: a launcher, the installer,
the login unit, a hook, or a command the docs tell a person to install. Those are the
``[project.scripts]`` of ``pyproject.toml``. Every other command is in `ROUTED` and has no script
of its own, so a shell has one front door and tab-completion on ``poolhouse-`` lists only what
must be there.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import distribution
from pathlib import Path

from poolhouse.cli.reference import HELP, TABLE, Row

PREFIX = "poolhouse-"
RUNTIME = "poolhouse.runtime_cli:main"

ROUTED: dict[str, str] = {
    "runtime": RUNTIME,
    "help": "poolhouse.cli:help_main",
    "open": "poolhouse.fleet.launch_open:main",
    "local-network": "poolhouse.node_permission:main",
    "device": "poolhouse.fleet.device_cli:main",
    "device-setup": "poolhouse.device_setup:main",
    "credentials": "poolhouse.credentials.cli:command",
    "features": "poolhouse.features_cli:main",
    "schedule": "poolhouse.schedule.cli:main",
    "test": "poolhouse.testfarm.cli:command",
    "test-devices": "poolhouse.testfarm.devices:command",
}
"""``word`` -> ``module:attr`` for the commands that run as ``poolhouse <word>`` and have no ``poolhouse-<word>``.

A console script of the same word is refused: a command is declared in one place."""

INTERNAL = frozenset({"traind"})
"""Commands ``poolhouse --list`` leaves out unless given ``--all``: a daemon, not something a person types."""


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
        if not word or word in found or word in ROUTED:
            raise ValueError(f"duplicate or reserved Poolhouse command: {name}")
        found[word] = Command(target, HELP.get(word, "Installed command; use its --help for details."),
                              public=word not in INTERNAL)
    for word, target in ROUTED.items():
        found[word] = Command(target, HELP[word])
    return found


def rows(pyproject: Path) -> tuple[Row, ...]:
    """`TABLE`, then a row from the registry's own summary for each command `TABLE` leaves out."""
    named = {row.word for row in TABLE}
    declared = registry(pyproject)
    return (*TABLE, *(Row(f"poolhouse {word}", declared[word].summary)
                      for word in sorted(declared) if word not in named))


def table(pyproject: Path) -> str:
    """The command table as Markdown, exactly as `docs/commands.md` carries it."""
    head = "| | |\n| --- | --- |"
    body = "\n".join(f"| `{row.invocation}` | {row.about} |" for row in rows(pyproject))
    return f"{head}\n{body}"

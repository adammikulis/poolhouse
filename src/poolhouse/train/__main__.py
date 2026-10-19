"""``python -m poolhouse.train run|tools|decider|suite``: the training commands."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from importlib import import_module

from poolhouse.log import warn

COMMANDS = {"run": "poolhouse.train.run", "tools": "poolhouse.train.tools",
            "decider": "poolhouse.train.decider_cli", "suite": "poolhouse.train.suite"}
"""The word that picks a command, and the module whose ``main`` runs it."""


def dispatch(argv: Sequence[str] | None = None) -> int:
    """Run the command the first word names with the rest."""
    words = list(sys.argv[1:] if argv is None else argv)
    if not words or words[0] not in COMMANDS:
        warn(f"usage: python -m poolhouse.train {{{'|'.join(COMMANDS)}}} [args...]")
        return 2
    return int(import_module(COMMANDS[words[0]]).main(words[1:]) or 0)


raise SystemExit(dispatch())

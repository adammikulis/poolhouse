"""State roots and startup output for a pool daemon process."""

from __future__ import annotations

import os
from pathlib import Path

from poolhouse import caller, home
from poolhouse.log import say

DAEMON_ENV = "POOLHOUSE_DAEMON_ROOT"
"""Set by a daemon for itself and what it starts: the directory its settings are saved in."""


def default_root() -> Path:
    """Return the daemon directory under the machine state root."""
    return home.state("traind")


def settings_root() -> Path:
    """The daemon directory whose saved settings govern this process: the one of the daemon that started it, else the
    default."""
    held = os.environ.get(DAEMON_ENV)
    return home.expand(held) if held else default_root()


def configure(root: Path) -> None:
    """Route custom daemon installations to their own process state and cache."""
    os.environ[DAEMON_ENV] = str(root.resolve())
    if os.environ.get(home.ROOT_ENV) or root.resolve() == home.state("traind").resolve():
        return
    state = root.resolve() / "state"
    os.environ[home.ROOT_ENV] = str(state)
    if not os.environ.get(home.CACHE_ENV):
        os.environ[home.CACHE_ENV] = str(state / "cache")


def announce_token(token: str) -> None:
    """Display the daemon token unless an agent runs this: its output is read by the agent's model."""
    if not caller.marked():
        say(f"  token {token}")

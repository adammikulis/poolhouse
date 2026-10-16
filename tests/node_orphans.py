"""Test nodes left running by a pytest that was killed.

A kit that starts a node on a temporary root writes the pid of its pytest process into the root (`claim`). The next session finds
every such root whose pytest is gone, stops the node under it and removes the root (`reap`), so a killed run does not leave a node
holding ports, memory and a state directory for days.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from poolhouse import node_launch
from poolhouse.lock import pid_alive

OWNER = "owner.pid"
PREFIX = "ml"


def claim(root: Path) -> None:
    """Record this process as the owner of the node under ``root``."""
    (root / OWNER).write_text(str(os.getpid()))


def owner_of(root: Path) -> int | None:
    """The pid recorded in ``root``, or None when it records none."""
    try:
        return int((root / OWNER).read_text().strip())
    except (OSError, ValueError):
        return None


def reap(base: Path = Path("/tmp")) -> list[Path]:
    """Stop the node under, and remove, every root in ``base`` whose recorded owner is no longer running; the roots reaped."""
    reaped = []
    for root in sorted(base.glob(f"{PREFIX}*")):
        pid = owner_of(root)
        if pid is None or pid == os.getpid() or pid_alive(pid):
            continue
        state = root / "node"
        if state.is_dir() and not state.is_symlink():
            node_launch.stop_node(state)
        shutil.rmtree(root, ignore_errors=True)
        reaped.append(root)
    return reaped

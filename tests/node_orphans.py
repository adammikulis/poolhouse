"""Reap recorded test nodes inside a caller-owned directory."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from poolhouse import node_launch, node_supervise
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


def answers_as_recorded(state: Path) -> bool:
    """Whether a node answers on ``state`` under the pid its run record names; a recorded pid that nothing answers for is not signalled."""
    said = node_launch.node_health(state)
    return said is not None and isinstance(pid := node_supervise.running(state).get("pid"), int) and said.get("pid") == pid


def reap(base: Path) -> list[Path]:
    """Stop the node under, and remove, every root in ``base`` whose recorded owner is no longer running; the roots reaped."""
    reaped = []
    for root in sorted(base.glob(f"{PREFIX}*")):
        pid = owner_of(root)
        if pid is None or pid == os.getpid() or pid_alive(pid):
            continue
        state = root / "node"
        if state.is_dir() and not state.is_symlink() and answers_as_recorded(state):
            node_launch.stop_node(state)
        shutil.rmtree(root, ignore_errors=True)
        reaped.append(root)
    return reaped

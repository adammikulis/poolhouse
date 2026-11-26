"""Whether a process began before the selected runtime was verified, and so still runs older code.

`poolhouse runtime ensure` switches what the next start runs and signals nothing, because a store writer is never killed. The
broker asks this of itself (every look for new servers) and `ensure` asks it of the broker, so both act on one answer; `runtime
status` names every process for which it holds (`poolhouse.runtime_stale`).
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import psutil

from poolhouse import runtime
from poolhouse.files import read_json

__all__ = ["MARK", "Running", "is_older", "older_than", "verified_at"]

MARK = "verified.json"
"""The file in a runtime prefix that records when it passed its smoke."""
SLACK_S = 5.0
"""A process that started within this long of the verification is not older than it."""


class Running(NamedTuple):
    """One process: its id, when it started (epoch seconds) and its command line."""

    pid: int
    started: float
    argv: tuple[str, ...]


def verified_at(prefix: Path) -> float:
    """When a prefix passed its smoke, or 0.0 when it never did."""
    row = read_json(prefix / MARK, {})
    value = row.get("verified_at") if isinstance(row, dict) else None
    return float(value) if isinstance(value, (int, float)) else 0.0


def older_than(procs: list[Running], prefix: Path, verified_at: float) -> list[Running]:
    """The processes that began before ``verified_at`` and do not run from ``prefix`` (the selected runtime).

    A host that replaced itself with `execve` keeps its start time but its command line names the
    selected runtime's interpreter, so it is not listed.
    """
    if verified_at <= 0:
        return []
    inside = str(prefix)
    return [p for p in procs if p.started + SLACK_S < verified_at and not any(inside in word for word in p.argv)]


def is_older(pid: int | None = None) -> bool:
    """Whether the process ``pid`` (this one by default) runs code older than the selected runtime. `False` when no runtime is
    selected or it was never verified, and for a process that cannot be read."""
    prefix = runtime.selection_prefix()
    if prefix is None:
        return False
    try:
        one = psutil.Process(pid)
        row = Running(one.pid, one.create_time(), tuple(one.cmdline()))
    except (psutil.Error, OSError):
        return False
    return bool(older_than([row], prefix, verified_at(prefix)))

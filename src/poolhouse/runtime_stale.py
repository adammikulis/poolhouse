"""Processes that started before the selected runtime was verified, and so still run older code.

`poolhouse runtime ensure` switches what the next start runs; it signals nothing, because a store
writer is never killed. A daemon started before the switch keeps running the build it started
with (the board daemon once ran an old build for hours). This names those processes, so
`runtime status` can say so (`poolhouse.runtime_age` decides which they are).
"""

from __future__ import annotations

from pathlib import Path

from poolhouse.runtime_age import Running, older_than
from poolhouse.serve import process

__all__ = ["Running", "older_than", "running", "stale_lines"]

MARKERS = ("poolhouse", "poolhouse")
"""A process is one of ours when a word of its command line holds one of these."""


def running() -> list[Running]:
    """Every readable process of ours (a command-line word names poolhouse or a poolhouse program), this one excluded."""
    return [Running(*row) for row in process.command_lines() if any(m in word for word in row[2] for m in MARKERS)]


def stale_lines(prefix: Path, verified_at: float, procs: list[Running] | None = None) -> list[str]:
    """One status line per process older than the selected runtime, then the line that says what to do."""
    old = older_than(running() if procs is None else procs, prefix, verified_at)
    lines = [f"older        pid {p.pid} began {int(verified_at - p.started)}s before the selected runtime: {' '.join([Path(p.argv[0]).name, *p.argv[1:]])[:100]}"
             for p in old]
    if old:
        lines.append(f"note         {len(old)} process(es) run code older than the selected runtime; "
                     "stop them through their own control (never a kill) and start them again")
    return lines

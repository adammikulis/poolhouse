"""The exit statuses of a run, and the one status a set of causes gives."""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["BUSY", "CANCELLED", "FAILED", "NOT_RUN", "NOT_STARTED", "OK", "REFUSED", "TIMED_OUT", "status_of"]

OK = 0
FAILED = 1
REFUSED = 4
BUSY = 6
NOT_RUN = 70
NOT_STARTED = 75
TIMED_OUT = 124
CANCELLED = 130


def status_of(causes: Iterable[int]) -> int:
    """1 when any cause is a failure, else the highest cause present, else 0."""
    present = set(causes)
    if FAILED in present:
        return FAILED
    return max(present, default=OK)

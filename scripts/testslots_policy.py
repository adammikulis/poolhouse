"""Admission policy for the test broker: shortest estimated work first, with aging so no request waits forever."""
from __future__ import annotations

import os
import sys

INTERACTIVE = "interactive"
BACKGROUND = "background"
CLASSES = (INTERACTIVE, BACKGROUND)
VERSION = 2
BACKFILLS = 2
"""How many later requests may take idle capacity ahead of a head request that does not fit before the head holds it back."""
BACKFILL_WAIT_S = 30.0
"""How long a head request waits before it holds idle capacity back from later requests."""
BACKGROUND_MAX_WAIT_S = 600.0
UNKNOWN_BACKGROUND_S = 180.0
BACKGROUND_NICE = 10
NICE_EXEC = ("import os, sys\n"
             "try:\n    os.nice(int(sys.argv[1]))\nexcept OSError:\n    pass\n"
             "os.execvp(sys.argv[2], sys.argv[2:])")


def max_wait() -> float:
    """Seconds a request waits before it is granted ahead of the ordering (DEV_TEST_BACKGROUND_WAIT_S)."""
    raw = os.environ.get("DEV_TEST_BACKGROUND_WAIT_S", "")
    return float(raw) if raw.replace(".", "", 1).isdigit() else BACKGROUND_MAX_WAIT_S


def backfill_wait() -> float:
    """Seconds a head request waits before later requests stop taking idle capacity ahead of it (DEV_TEST_BACKFILL_WAIT_S)."""
    raw = os.environ.get("DEV_TEST_BACKFILL_WAIT_S", "")
    return float(raw) if raw.replace(".", "", 1).isdigit() else BACKFILL_WAIT_S


def reserved(head: dict, now: float) -> bool:
    """Whether a head request that does not fit holds idle capacity back: after ``BACKFILLS`` later requests passed it or ``backfill_wait`` seconds."""
    return head.get("backfills", 0) >= BACKFILLS or now - float(head.get("since", now)) > backfill_wait()


def run_class(data: dict) -> str:
    """The class recorded in a lease record; records without one are interactive."""
    return BACKGROUND if data.get("class") == BACKGROUND else INTERACTIVE


def environment_class(environment: dict) -> str:
    """The class a run's environment (DEV_TEST_CLASS) asks for."""
    return run_class({"class": environment.get("DEV_TEST_CLASS")})


def has_legacy(slots: list) -> bool:
    """Whether a live lease predates this policy; while one is queued or running every lease keeps arrival order."""
    return any(int(slot.data.get("version", 1)) < VERSION for slot in slots)


def estimate(data: dict) -> float:
    """Estimated work in seconds; a record without one counts as 0 when interactive and 180 when background."""
    value = data.get("estimate", data.get("estimate_s"))
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0 if run_class(data) == INTERACTIVE else UNKNOWN_BACKGROUND_S


def aged(data: dict, now: float) -> bool:
    """Whether a request has waited past the bound."""
    return now - float(data.get("since", now)) > max_wait()


def sort_key(data: dict, now: float) -> tuple:
    """Queue order: aged requests first by arrival, then by estimated work less the seconds waited."""
    since = float(data.get("since", now))
    return (0, since) if aged(data, now) else (1, estimate(data) - (now - since))


def order(waiting: list, now: float) -> list:
    """Waiting slots in grant order."""
    return sorted(waiting, key=lambda slot: (sort_key(slot.data, now), slot.path))


def lower_priority(command: list[str], klass: str) -> list[str]:
    """The command wrapped to start at lower CPU priority when the class is background; unchanged without os.nice."""
    if klass != BACKGROUND or not hasattr(os, "nice"):
        return command
    return [sys.executable, "-c", NICE_EXEC, str(BACKGROUND_NICE), *command]

"""Post runtime deploy outcomes and incidents to the board as the session of the deploying agent."""

from __future__ import annotations

import re

from poolhouse import hook_diagnostics
from poolhouse.board.client import NodeError
from poolhouse.board.session import Session
from poolhouse.runtime_deploy import Outcome
from poolhouse.workspace import board_cli
from poolhouse.workspace.screen import Refused

COMMIT = re.compile(r"[0-9a-f]{40}")
LIMIT = 190
SHORT = 7


def _text(outcome: Outcome, previous: str, verb: str) -> tuple[str, str]:
    """The announcement kind and one line for an outcome."""
    now = outcome.commit[:SHORT]
    was = previous[:SHORT] if COMMIT.fullmatch(previous) else "none"
    if outcome.action == "switched":
        return "milestone", f"runtime {verb} {now} (was {was})"
    if outcome.action == "recovered":
        return "blocked", f"runtime recovered from a broken selection; {now} wanted, {was} was selected"
    return "blocked", f"runtime {verb} of {now} failed; {was} stays selected"


def incident(outcome: Outcome, stage: str) -> str:
    """Record a redacted hook-diagnostic for a failed or recovered deploy and return its id, or empty."""
    note = hook_diagnostics.record(RuntimeError(outcome.detail or outcome.action), "runtime-deploy", stage,
                                   metadata={"action": outcome.action, "commit": outcome.commit})
    found = re.search(r"diagnostic=([0-9a-f]{32})", note)
    return found[1] if found else ""


def announce(outcome: Outcome, previous: str, who: Session | None, *, verb: str = "ensure") -> bool:
    """Post one announcement for a switch, recovery or failure as the session ``who``; True when the board took it."""
    if who is None or outcome.action in {"current", "busy", "held"} or not COMMIT.fullmatch(outcome.commit):
        return False
    kind, text = _text(outcome, previous, verb)
    if kind == "blocked":
        found = incident(outcome, verb)
        text = f"{text} [diagnostic={found}]" if found else text
    try:
        board_cli.announced(who, kind, text[:LIMIT])
    except (Refused, NodeError, OSError, ValueError):
        return False
    return True

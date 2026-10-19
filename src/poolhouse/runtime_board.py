"""Post a failed or recovered runtime deploy to the board as the session of the deploying agent; a switch to a new build posts nothing."""

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


def _text(outcome: Outcome, previous: str, verb: str) -> str:
    """The `blocked` line for a recovered or failed outcome."""
    now = outcome.commit[:SHORT]
    was = previous[:SHORT] if COMMIT.fullmatch(previous) else "none"
    if outcome.action == "recovered":
        return f"runtime recovered from a broken selection; {now} wanted, {was} was selected"
    return f"runtime {verb} of {now} failed; {was} stays selected"


def incident(outcome: Outcome, stage: str) -> str:
    """Record a redacted hook-diagnostic for a failed or recovered deploy and return its id, or empty."""
    note = hook_diagnostics.record(RuntimeError(outcome.detail or outcome.action), "runtime-deploy", stage,
                                   metadata={"action": outcome.action, "commit": outcome.commit})
    found = re.search(r"diagnostic=([0-9a-f]{32})", note)
    return found[1] if found else ""


def announce(outcome: Outcome, previous: str, who: Session | None, *, verb: str = "ensure") -> bool:
    """Post one `blocked` announcement for a recovery or failure as the session ``who``; True when the board took it."""
    if who is None or outcome.action not in {"recovered", "failed"} or not COMMIT.fullmatch(outcome.commit):
        return False
    text = _text(outcome, previous, verb)
    found = incident(outcome, verb)
    text = f"{text} [diagnostic={found}]" if found else text
    try:
        board_cli.announced(who, "blocked", text[:LIMIT])
    except (Refused, NodeError, OSError, ValueError):
        return False
    return True

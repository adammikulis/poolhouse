"""Who may schedule a job: an agent session in no sandbox, or the person.

A stored job runs later with the account's own reach, so making one is how a process could run what it may not run itself. The
decision is made at once from what the operating system and the node say of the caller, never from what the caller says:

* a process under any sandbox is refused, one that only denies a few things too: its job would run without it;
* what an unattended agent's runner started (a delegate, a board's local agent) is refused;
* the node reads the caller's process tree: an agent it places in a session passes, a lead or a helper; so does a terminal
  outside every agent; a process marked as an agent's that it places in no session, and a caller it cannot read, are refused.

No page and no network request creates a job: the app only shows and cancels them. Each decision is in the authority audit log.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Mapping
from typing import Any

from poolhouse import caller, home
from poolhouse.isolation import ancestry
from poolhouse.sandbox import confinement

__all__ = ["admit", "refused_here"]

SANDBOXED = ("Refused: this process runs in a sandbox, and a scheduled job runs later outside it. Schedule it from a session that "
             "is not sandboxed, or from a terminal.")
UNATTENDED = ("Refused: an agent that runs with nobody at it (a delegate, or a local agent of a board), and whatever it starts, "
              "schedules nothing.")
UNPLACED = "Refused: this process is marked as an agent's and the node finds no session above it, so it schedules nothing."


def _sandboxed() -> bool:
    """Whether any sandbox applies to this process. `confinement.confined` asks only for one that refuses by default; here one that
    allows by default counts too, and a check that cannot be made counts as a sandbox."""
    if confinement.confined():
        return True
    if sys.platform != "darwin":
        return False
    try:
        return confinement.seatbelt_state(os.getpid()) != "none"
    except (OSError, AttributeError):
        return True


def refused_here() -> str:
    """Why this process may neither schedule a job nor start the process of one, "" when it may be asked further."""
    if _sandboxed():
        return SANDBOXED
    return UNATTENDED if ancestry.unattended() else ""


def _refuse(action: str, why: str) -> caller.AgentRefused:
    with contextlib.suppress(OSError):  # a sandbox may hold no write to the state root: the refusal still stands
        caller.record("refused", action=action, why=why)
    return caller.AgentRefused(why)


@home.real_roots
def admit(action: str, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Let this caller do ``action`` and return who it is, or raise `AgentRefused` at once, audited."""
    if why := refused_here():
        raise _refuse(action, why)
    try:
        where = ancestry.attest()
    except ancestry.Unattested as why:
        raise _refuse(action, str(why)) from why
    if not where.may_launch(env):
        raise _refuse(action, UNPLACED)
    sessions = [{"board": each.board, "name": each.name, "parent": each.parent, "harness": each.harness} for each in where.sessions]
    return {"who": caller.who(env), "sessions": sessions}

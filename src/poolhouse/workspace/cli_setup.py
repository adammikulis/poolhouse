"""The doctor, hello and snippet commands of the workspace CLI."""

from __future__ import annotations

import argparse

from poolhouse.board.session import Session
from poolhouse.command import flag
from poolhouse.log import say
from poolhouse.workspace import onboard

__all__ = ["TABLE", "doctor", "hello", "snippet"]


def doctor(args: argparse.Namespace, s: Session) -> int:
    """Check the whole setup and say what to fix."""
    found = onboard.doctor(s)
    for f in found:
        say(("ok   " if f.ok else "FIX  ") + f.what + ("" if f.ok else f"  -> {f.fix}"))
    return 0 if all(f.ok for f in found) else 1


def hello(args: argparse.Namespace, s: Session) -> int:
    """Send the first message to an agent."""
    say(f"sent message {onboard.hello(s, args.name)['seq']} to {args.name}")
    return 0


def snippet(args: argparse.Namespace) -> int:
    """Print the paste block for an agent."""
    say(onboard.snippet(args.name), end="")
    return 0


TABLE = (
    ("doctor", "check the setup of this session and say what to fix", [], doctor),
    ("hello", "send AGENT the first message ('workspace ready')", [flag("name")], hello),
)

"""`poolhouse pool found`: an agent makes this device's pool, and the person answers where only they can (docs/api.md, `ph.pool.found`).

The command is for the agent; the owner says "make a pool called home" and never types it. It prints one sentence a person can read, and on
a refusal the sentence, where to tap and what the agent does next, with exit status 3 so a script knows the person has something to do.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from poolhouse import errors, node_launch
from poolhouse.board import founding, presence
from poolhouse.board.client import Client
from poolhouse.fleet import recovery
from poolhouse.log import say, warn

NEEDS_PERSON = 3


def add_commands(sub: Any) -> None:
    """Register ``found`` on a subparsers object."""
    p = sub.add_parser("found", help="make this device's pool, governed by the person's key; the person answers on their phone or at "
                       "this computer's prompt (an agent's command)")
    p.add_argument("name", nargs="?", default=None, help="the pool's name (default: this computer's)")
    p.add_argument("--policy", choices=("secure", "open"), default="secure",
                   help="secure: devices join with a code (default); open: devices on this network join by themselves")
    p.add_argument("--json", action="store_true", help="print the result as JSON")


def add_all(sub: Any) -> None:
    """Register every command this module and `recovery` add to the pool command."""
    recovery.add_commands(sub)
    add_commands(sub)


def run(args: argparse.Namespace) -> int:
    """Found the pool; the exit status (0 founded or already there, 3 when the person has something to do first)."""
    try:
        made = founding.found_here(Client(node_launch.default_state()), args.name, args.policy, voice=presence.Voice(say=say))
    except founding.Refusal as refused:
        if args.json:
            say(json.dumps({"founded": False, "say": str(refused), "needs": refused.needs, "where": refused.where, "then": refused.then}))
        else:
            warn(f"{refused} Where: {refused.where or 'nowhere yet'}. Next: {refused.then}.")
        return NEEDS_PERSON
    except errors.Error as err:
        warn(str(err))
        return 1
    say(json.dumps({"founded": not made.already, "say": made.say(), "pool": made.pool, "name": made.name}) if args.json else made.say())
    return 0

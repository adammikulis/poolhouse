"""The `pool` command of `poolhouse-workspace`: an agent founds this device's pool for a person (docs/api.md, `ph.pool.found`).

The owner says "make a pool called home" and never types this. It prints one sentence a person can read; when the person has something to
do first (answer on the phone, link the phone) it prints the sentence, where they do it and what the agent does next, and exits 3.
"""

from __future__ import annotations

import argparse
import json

from poolhouse import node_launch
from poolhouse.board import founding, presence
from poolhouse.board.client import Client
from poolhouse.command import flag
from poolhouse.log import say, warn
from poolhouse.workspace import board_cli

__all__ = ["COMMAND", "OPTIONS"]

NEEDS_PERSON = 3
OPTIONS = [
    flag("verb", choices=("found",), help="found: make this device's pool, governed by the person's key"),
    flag("name", nargs="?", default=None, help="found: the pool's name (default: this computer's)"),
    flag("--policy", choices=("secure", "open"), default="secure",
         help="found: secure, devices join with a code (default); open, devices on this network join by themselves"),
]


def _refused(refused: founding.Refusal, as_json: bool) -> int:
    if as_json:
        say(json.dumps({"founded": False, "say": str(refused), "needs": refused.needs, "where": refused.where, "then": refused.then}))
    else:
        warn(f"{refused} Where: {refused.where or 'nowhere yet'}. Next: {refused.then}.")
    return NEEDS_PERSON


def command(args: argparse.Namespace) -> int:
    """Found the pool; the exit status (0 founded or already there, 3 when the person has something to do first)."""
    try:
        made = founding.found_here(Client(node_launch.default_state()), args.name, args.policy, voice=presence.Voice(say=say))
    except founding.Refusal as refused:
        return _refused(refused, bool(args.json))
    say(json.dumps({"founded": not made.already, "say": made.say(), "pool": made.pool, "name": made.name}) if args.json else made.say())
    return 0


COMMAND = board_cli.guarded(command)

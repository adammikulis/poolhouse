"""The `approval` command of `poolhouse-workspace`: ask for grants, and have the decision at once.

`request` is decided by policy before it returns (`poolhouse.board.autoapprove`): this device's key signs what the caller may have, and the rest is
refused with the reason, before any request exists. There is nothing to wait for, read back or cancel.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from poolhouse.board import autoapprove
from poolhouse.board.approval import Approval, Ask
from poolhouse.command import flag
from poolhouse.log import say
from poolhouse.workspace import board_cli

__all__ = ["COMMAND", "OPTIONS"]

VERBS = ("request",)
OPTIONS = [
    flag("verb", choices=VERBS, help="request ACTION[:RESOURCE]..."),
    flag("items", nargs="*", help="the asks: ACTION or ACTION:RESOURCE"),
    flag("--for", dest="lasts", default="1h", metavar="TIME", help="how long each grant lasts once approved: 90m, 12h, 30d (default 1h)"),
    flag("--uses", type=int, default=0, help="how often each may be used (default: as often as it is needed)"),
    flag("--review", action="store_true", help="each use needs an independent review behind it"),
    flag("--note", default="", help="your own words for the request, up to 160 plain characters (shown as yours, checked by nobody)"),
    flag("--task", default="", metavar="REF", help="the task on the board the request came from"),
    flag("--expect-policy", default="", choices=("", "open", "secure"), help="what you believe the pool's join policy is; the node refuses the request if it is not"),
]


def _asks(args: argparse.Namespace) -> list[Ask]:
    if not args.items:
        raise ValueError("name what to ask for: ACTION or ACTION:RESOURCE, for example add_device or link_write:demo>lab")
    return [Ask(action, resource or "*", args.lasts, args.uses or None, args.review) for action, _, resource in (i.partition(":") for i in args.items)]


def _shown(a: Approval) -> dict[str, Any]:
    return {"id": a.id, "state": a.state, "message": a.message(), "risk": a.risk, "grants": list(a.grants), "text": a.text}


def command(args: argparse.Namespace) -> int:
    """The `approval request` command."""
    made = autoapprove.request(board_cli.connect(args), _asks(args), note=args.note, task=args.task, expect_policy=args.expect_policy)
    if args.json:
        say(json.dumps(_shown(made)))
    else:
        say(f"{made.id}  {made.state}: {made.message()}\n\nwhat the request says:\n{made.text}")
    return 0 if made.state == "approved" else 3


COMMAND = board_cli.guarded(command)

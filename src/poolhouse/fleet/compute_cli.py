"""``poolhouse-pool plan | pause | resume``: manage compute across the devices of this node's pool.

The devices are the ones the Rust node lists (`roster.members`); nothing here knows who may join.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from typing import Any

from poolhouse.log import say, warn
from poolhouse.serve.fit import records
from poolhouse.serve.profile import profiles

from . import roster
from .applying import apply_plan, serving_table
from .onboard.lan import DEFAULT_HTTP_PORT
from .pausing import Fanout, minutes_of, pause_fleet, pause_table
from .plan import PREFERENCES, place, table as plan_table
from .runtime_paths import default_root

__all__ = ["add_commands", "cmd_pause", "cmd_plan", "table"]

NODE_DOWN = ("The pool is the node's: start it, then join with 'poolhouse-pool join --host HOST' "
             "or found one with 'poolhouse-pool found NAME'.")


def add_commands(commands: Any) -> None:
    """Add ``plan``, ``pause`` and ``resume`` to a subparsers object."""
    for name in ("plan", "pause", "resume"):
        command = commands.add_parser(name)
        command.set_defaults(cmd=name)
        command.add_argument("--json", action="store_true")
        command.add_argument("--root", default=None, help="the daemon's state folder (default: the installed one)")
        command.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
        command.add_argument("--timeout", type=float, default=2.0)
        if name == "plan":
            command.add_argument("--users", type=int, required=True)
            command.add_argument("--context", type=int, required=True)
            command.add_argument("--prefer", choices=sorted(PREFERENCES), default="quality")
            command.add_argument("--apply", action="store_true")
        elif name == "pause":
            command.add_argument("--for", dest="span", default="")
            command.add_argument("--reason", default="")


def table(rows: Sequence[dict[str, Any]]) -> str:
    """The devices, as text."""
    if not rows:
        return ("no device answered.\n"
                "  - is the daemon running there?  poolhouse\n"
                "  - is it a member?  poolhouse-pool members")
    lines = [f"{'NAME':<16} {'URL':<28} {'ROOM':<16} {'STATE':<12} {'COMMIT':<9} SERVING"]
    for r in rows:
        state = "busy" if r["busy"] or r["free"] == 0 else "idle"
        if r.get("queued"):
            state += f" +{r['queued']}"
        if r.get("lock"):
            state = "measuring"
        if r.get("paused"):
            state = "paused"
        commit = str(r.get("commit") or "?").split()[0][:7] or "?"
        lines.append(f"{r['name']:<16} {r['base_url']:<28} {r['room']:<16} {state:<12} "
                     f"{commit:<9} {', '.join(r['serving']) or '-'}")
    return "\n".join(lines)


def _devices(args: argparse.Namespace) -> list[dict[str, Any]] | None:
    """The answering devices, or None after saying that the node does not answer."""
    try:
        return roster.members(timeout_s=args.timeout, port=args.port)
    except PermissionError as exc:
        warn(f"{exc} {NODE_DOWN}")
        return None


def _measurements() -> tuple[list[Any], list[Any]]:
    """Every record of what scored best and every memory record this machine knows."""
    return profiles(), records()


def cmd_plan(args: argparse.Namespace) -> int:
    rows = _devices(args)
    if rows is None:
        return 1
    servings, fits = _measurements()
    placement = place(args.users, args.context, rows, servings, fits, prefer=args.prefer)
    applied: list[dict[str, Any]] = []
    if args.apply and placement.rows:
        applied = apply_plan(placement, rows, root=args.root,
                             say=(lambda line: None) if args.json else print)
    if args.json:
        say(json.dumps({**placement.as_dict(), "applied": applied}, indent=1))
    else:
        if not rows:
            say(table(rows))
        say(plan_table(placement))
        if applied:
            say()
            say(serving_table(applied))
    return 0 if placement.rows and not placement.unplaced else 1


def cmd_pause(args: argparse.Namespace) -> int:
    rows = _devices(args)
    if rows is None:
        return 1
    resume = args.cmd == "resume"
    span = str(getattr(args, "span", "") or "")
    minutes = minutes_of(span)
    if span and minutes is None:
        warn(f"{span!r} is not a length of time; try '2h', '90m' or '45s'")
        return 2
    answers = pause_fleet(Fanout(args.root or default_root(), resume=resume, minutes=minutes,
                                 reason=str(getattr(args, "reason", "") or "")), rows)
    if args.json:
        say(json.dumps([a.public() for a in answers], indent=1))
    else:
        say(pause_table(answers, resume=resume, span=span))
    return 0 if answers and all(a.ok for a in answers) else 1

"""Availability and working hours for one selected pool device."""
from __future__ import annotations

import argparse

from poolhouse.command import Group, flag
from poolhouse.fleet.discovery import DiscoveryError, derive_token
from poolhouse.fleet.members import _require_key
from poolhouse.fleet.remote import Discovery, Member
from poolhouse.http import ServerError
from poolhouse.log import say, warn


def _member(args: argparse.Namespace) -> Member:
    key = _require_key(args.pool_key)
    if args.name:
        return Member.find_one(name=args.name, discovery=Discovery(key=key))
    return Member(f"http://127.0.0.1:{args.port}", derive_token(key))


def _pause(args: argparse.Namespace) -> int:
    out = _member(args).availability("pause", minutes=args.minutes, reason=args.reason)
    say(out["unavailable_because"])
    if args.minutes:
        say(f"  it will start taking work again on its own in {args.minutes:.0f} min")
    else:
        say("  run 'poolhouse device resume' when you are done with it")
    return 0


def _resume(args: argparse.Namespace) -> int:
    out = _member(args).availability("resume")
    say("taking work again" if out["available"]
        else f"still not taking work: {out['unavailable_because']}")
    return 0


def _schedule(args: argparse.Namespace) -> int:
    if ((args.action in ("busy", "free") and not args.when)
            or (args.action == "clear" and args.when)):
        warn("schedule busy/free requires hours; schedule clear takes no hours")
        return 2
    member = _member(args)
    if args.action == "clear":
        member.availability("clear_windows")
        say("cleared; it will take work at any hour")
        return 0
    if args.action:
        out = member.availability("window", spec=args.when, busy=args.action == "busy")
        say("schedule now:")
    else:
        out = member.availability()
        say("available now" if out["available"] else f"NOT available: {out['unavailable_because']}")
    for window in out.get("windows", []):
        say(f"  busy  {window}")
    if not args.action:
        if out.get("reserved"):
            held = out["reserved"]
            say(f"  held by {held['holder']} for another {held['seconds_left']:.0f}s")
        if not out.get("windows") and not out.get("reserved") and out["available"]:
            say("  no schedule set -- it will take work at any hour")
    return 0


TARGET = (flag("--name", default="", help="another device in this pool (default: this device)"),
          flag("--port", type=int, default=8770))
COMMAND = Group("poolhouse device", "Availability and working hours for one selected pool device.",
                options=[flag("--pool-key", default=None, help="path to this pool's key")],
                errors=(DiscoveryError, ServerError, OSError),
                epilog="First-time device preparation: poolhouse device setup --help")
COMMAND.add("pause", _pause, help="stop taking work; the device's configured stop/leave policy governs running jobs",
            options=[*TARGET, flag("--minutes", type=float, default=0, help="resume after this many minutes"),
                     flag("--reason", default="", help="shown to the pool")])
COMMAND.add("resume", _resume, help="start taking work again", options=TARGET)
COMMAND.add("schedule", _schedule, help="show working hours, mark busy/free hours, or clear them",
            options=[*TARGET, flag("action", nargs="?", choices=("busy", "free", "clear")),
                     flag("when", nargs="?", default="", help="hours, for example mon-fri 09:00-17:00")])


main = COMMAND.run

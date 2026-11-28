"""``poolhouse phone``: linked phones. ``hook EVENT`` runs one hook of a connected project (`poolhouse.phone_hook`) on the harness's
event, read from standard input."""

from __future__ import annotations

import argparse
import json
import sys

from poolhouse import phone_hook
from poolhouse.command import Group, flag
from poolhouse.phone_mailbox import Store

__all__ = ["main"]

COMMANDS = Group("poolhouse-phone", "Phones linked to this computer.", errors=(OSError, RuntimeError, ValueError))
main = COMMANDS.run


@COMMANDS.command("hook", help="run one hook of a project connected to linked phones; the harness calls it",
                  options=[flag("event", choices=sorted(phone_hook.EVENTS))])
def hook(args: argparse.Namespace) -> int:
    """Status 2 wakes the session with what is on standard error; any failure is reported and lets the session go on."""
    try:
        event = json.loads(sys.stdin.read() or "{}")
        return phone_hook.run(args.event, event if isinstance(event, dict) else {}, Store(), sys.stdout, sys.stderr)
    except (OSError, ValueError, RuntimeError) as why:
        sys.stderr.write(f"poolhouse phone hook {args.event}: {type(why).__name__}: {why}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

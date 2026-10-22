"""``poolhouse-test-devices``: the pool's other devices, whether each takes tests, and its last result here."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from poolhouse.activity import reuse
from poolhouse.board.client import NodeError
from poolhouse.command import Group, flag, option
from poolhouse.log import say
from poolhouse.workspace import testruns

from . import report
from .client import ShardError, choose
from .farm import Farm, open_farm
from .ledger import Ledger


def ago(then: float) -> str:
    """``3m ago`` style age of a timestamp."""
    seconds = max(0, int(time.time() - then))
    for size, word in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds >= size:
            return f"{seconds // size}{word} ago"
    return f"{seconds}s ago"


def last_line(last: dict) -> str:
    """One device's last recorded run, or a dash."""
    if not last:
        return "-"
    return f"{'PASS' if last['exit'] == 0 else 'FAIL'} {last['tier']} {last['passed']} passed {last['failed']} failed {ago(last['at'])}"


def describe(shards: Farm, device: dict, ledger: Ledger) -> dict:
    """One row: the device, what it says about taking shards, and the last run recorded for it."""
    row = {**device, "last": ledger.last(device["fingerprint"])}
    try:
        row["caps"] = shards.capability(device["fingerprint"])
    except ShardError as exc:
        row["caps"] = {"accepts": False, "reason": f"did not answer: {exc}"}
    return row


def table(rows: list[dict]) -> list[str]:
    """The lines `poolhouse-test-devices` prints."""
    lines = [f"{'DEVICE':<20} {'VIA':<5} {'REACH':<8} {'PLATFORM':<34} {'SLOTS':<6} {'TAKES TESTS':<12} LAST RESULT"]
    for row in rows:
        caps = row["caps"]
        platform = report.where(caps["platform"], caps.get("python")) if "platform" in caps else "-"
        slots = f"{caps['free']}/{caps['most_active']}" if "free" in caps else "-"
        takes = "yes" if caps["accepts"] else "no"
        lines.append(f"{row['name']:<20} {row.get('via', 'node'):<5} {row.get('reach', 'unknown'):<8} {platform:<34} {slots:<6} {takes:<12} {last_line(row['last'])}")
        if not caps["accepts"]:
            lines.append(f"{'':<26} {caps.get('reason', '')}")
        if row.get("reach_hint"):
            lines.append(f"{'':<26} {row['reach_hint']}")
    return lines


def run(args: argparse.Namespace) -> int:
    """List the devices; 1 when no node answers or no registered session is available."""
    try:
        shards = open_farm()
        chosen = choose(args.device, shards.devices)
        ledger = Ledger(reuse.reuse_base() / testruns.scope(Path.cwd()))
        rows = [describe(shards, device, ledger) for device in chosen]
    except (ShardError, NodeError) as exc:
        say(f"test-devices: {exc}")
        return 1
    say(json.dumps(rows, indent=2) if args.json else "\n".join(table(rows)))
    return 0


COMMAND = Group(
    "poolhouse-test-devices",
    "The pool's other devices, whether each takes tests, and its last result here.",
    options=(flag("device", nargs="?", default="all",
                  help="the device to list, by name or fingerprint (default: all)"),
             option("json", help="print the rows as JSON instead of the table")),
    run=run)
command = COMMAND.run


if __name__ == "__main__":
    sys.exit(command())

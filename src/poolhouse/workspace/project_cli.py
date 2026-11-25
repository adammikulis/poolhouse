"""The `project` command of `poolhouse-workspace`: the projects this computer holds, and the removal of one.

A launch registers the project it works in by itself (docs/design-isolated-launcher.md, 8.3); `remove` forgets one at once, so its folder is free
for another project, and is the lead's or a terminal's, as a launch is. Nothing is asked and nothing waits.
"""

from __future__ import annotations

import argparse
import json

from poolhouse.board import client as board_client
from poolhouse.command import flag
from poolhouse.isolation import ancestry, remove_project
from poolhouse.isolation.refusals import IsolationRefused
from poolhouse.log import say, warn
from poolhouse.workspace import board_cli

__all__ = ["COMMAND", "OPTIONS"]

VERBS = ("list", "remove")
OPTIONS = [
    flag("verb", choices=VERBS, help="list the projects of this computer, or remove PROJECT"),
    flag("project", nargs="?", default="", help="for remove: the project's id, or a folder of it (one that no longer exists too)"),
]


def _held() -> list[dict[str, object]]:
    return [dict(row) for row in board_client.Client(ancestry.node_state()).call("project_list")]


def command(args: argparse.Namespace) -> int:
    """The `project` command."""
    if args.verb == "list":
        rows = _held()
        say(json.dumps(rows) if args.json else "\n".join(f"{row['id']}  {', '.join(str(each['path']) for each in row['sources'])}" for row in rows)
            or "no projects")
        return 0
    if not args.project:
        raise ValueError("name the project to remove: its id, or a folder of it")
    try:
        board = remove_project(args.project)
    except IsolationRefused as why:
        warn(f"workspace: {why}")
        return 3
    say(json.dumps({"removed": board}) if args.json else f"removed {board}")
    return 0


COMMAND = board_cli.guarded(command)

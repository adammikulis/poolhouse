"""The `status` command of `poolhouse-workspace`: who is on the board, what waits, what is held and what is claimed."""

from __future__ import annotations

import argparse
from typing import Any

from poolhouse.board.session import Session
from poolhouse.workspace import (
    agent_display,
    autostart_status,
    board_cli,
    claims_cli,
    mail_cli,
    rooms_cli,
    slots,
)

__all__ = ["TABLE"]


def status(args: argparse.Namespace, s: Session) -> Any:
    """Who is registered, unread counts, claims and the test-slot queue; any session of the board."""
    registered = board_cli.agents(args, s)
    board = s.status()
    rooms = rooms_cli.listing(s)
    names = {row["id"] for row in registered}
    unread = len(s.read(s.cursor("inbox"), inbox=True).entries)
    return {
        "agents": sorted(names), "registered": registered, "coordinator_notice": agent_display.vacancy_notice(registered),
        "messages": board["entries"].get("message", 0), "notes": board["entries"].get("note", 0),
        "quarantined": sum(1 for item in s.held() if item["state"] == "quarantined"),
        "claims": [claims_cli.shown(c) for c in s.claims()], "chains_ok": not board["damaged"], "rejected": board["rejected"],
        "testslots": slots.testslots(lambda name: name in names), "unread": unread,
        "boards": [{"name": r["name"], "unread": r["unread"], "member": r["member"]} for r in rooms],
        "unread_lines": [f"{r['unread']} unread on {r['name']}" for r in rooms if r["unread"]],
        "dms_unread": sum(c["unread"] for c in mail_cli.conversations(s)),
        **autostart_status.fields(lambda kind, text: s.post("#announcements", kind, text, subject=kind)),
    }


TABLE = (("status", "who is registered, unread counts, claims, the test-slot queue; any agent token", [], status),)

"""Telling whoever scheduled a job how it ended: a workspace message to that session, from the schedule's own session.

A session that is gone, and a job a person scheduled in a terminal, are told in ``#announcements`` of the project's board. The
message is data like any other, nothing waits on an answer, and one that cannot be sent is tried again (`spawn.NOTICE_DAYS`).
"""

from __future__ import annotations

from typing import Any

from poolhouse import node_launch, schedule_record as record
from poolhouse.board import session as sessions
from poolhouse.board.client import Client, NodeError
from poolhouse.errors import Denied

__all__ = ["SENDER", "message", "send"]

SENDER = "schedule"
LIMIT = 900
SHORT = 190


def message(job: dict[str, Any]) -> str:
    """What the job came to, in one message."""
    steps = job.get("results") or {}
    tally = ", ".join(f"{sum(1 for one in steps.values() if one.get('state') == state)} {state}"
                      for state in ("ok", "failed", "skipped") if any(one.get("state") == state for one in steps.values()))
    name = f" ({job['name']})" if job.get("name") else ""
    said = f"Scheduled job {job['id']}{name}, due {record.when(job['at'])}, ended {job['state']}"
    said += f": {job['why']}" if job.get("why") else ""
    said += f". Steps: {tally}" if tally else ""
    return f"{said}. `poolhouse schedule show {job['id']}` shows it."[:LIMIT]


def send(job: dict[str, Any], client: Client | None = None) -> str:
    """Send the notice of ``job``; "" once the node took it, else why it did not."""
    by = job.get("by") or {}
    board, agent = str(by.get("board") or ""), str(by.get("agent") or "")
    try:
        node = client or Client(node_launch.default_state())
        if not board:
            return "no board is known for whoever scheduled it"
        made = sessions.register(node, board, sessions.Native("script", "python", SENDER))
        mine = sessions.Session(node, board, made.token, made.name)
        if agent:
            try:
                mine.post(agent, "note", message(job), subject="scheduled job")
                return ""
            except (Denied, NodeError):
                pass  # the session is gone: said to everybody on the board instead
        said = f"{f'for {agent}: ' if agent else ''}{message(job)}".replace("\n", " ")
        mine.post("#announcements", "milestone", said[:SHORT], subject="milestone")
    except (Denied, NodeError, OSError, ValueError, KeyError) as why:
        return str(why)[:300] or type(why).__name__
    return ""

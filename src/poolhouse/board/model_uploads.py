"""The workspace notice of `poolhouse.hub.uploads`: one announcement for each change the daily check finds."""

from __future__ import annotations

from collections.abc import Callable

from poolhouse.board import session as sessions
from poolhouse.board.client import Client, NodeError
from poolhouse.errors import Denied

SENDER = "model-uploads"
LIMIT = 190


def board_notice(boards: Callable[[], list[dict]], client: Client | None = None) -> Callable[[str], None]:
    """A poster that announces a line on one workspace: the first of ``boards`` whose project is here, a preferred one first.
    `LookupError` with none and `RuntimeError` when the node refuses, either of which leaves the line for the next check."""
    def post(text: str) -> None:
        here = sorted((row for row in boards() if row.get("available")), key=lambda row: not row.get("preferred"))
        if not here:
            raise LookupError("no workspace to post to")
        node, board = client or Client(), str(here[0]["id"])
        try:
            made = sessions.register(node, board, sessions.Native("script", "python", SENDER))
            sessions.Session(node, board, made.token, made.name).post(
                "#announcements", "milestone", text.replace("\n", " ")[:LIMIT], subject="milestone")
        except (Denied, NodeError) as exc:
            raise RuntimeError(str(exc)) from exc

    return post

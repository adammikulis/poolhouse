"""The person's own session on a board, for the UI daemon that acts for the person (docs/node.md, "The person").

The daemon registers one parentless session per board under the harness `poolhouse-ui`; the node names it like any
session and the person's grant (a session with no parent) is what the node checks. The token is kept in the
daemon's private client folder and never reaches a page.
"""

from __future__ import annotations

from pathlib import Path

from poolhouse.board import credentials, place, session as sessions
from poolhouse.board.client import Client, Denied

__all__ = ["HARNESS", "seat"]

HARNESS = "poolhouse-ui"
MODEL = "person"
NATIVE_ID = "person"


def seat(root: str | Path, client: Client | None = None) -> sessions.Session:
    """The person's session on the board of the project at ``root``, registered the first time.

    `Denied` when ``root`` is not part of a project the node can place.
    """
    node = client or Client()
    board = place.resolve(node, Path(root))
    name = sessions.find(HARNESS, NATIVE_ID, cwd=Path(root), client=node)
    if name:
        try:
            return sessions.Session(node, board, credentials.load(node.state, board, name), name)
        except Denied:
            pass
    made = sessions.register(node, board, sessions.Native(MODEL, HARNESS, NATIVE_ID))
    return sessions.Session(node, board, made.token, made.name)

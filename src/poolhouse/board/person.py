"""The person's own session on a board, for the UI daemon that acts for the person (docs/node.md, "The person").

The daemon registers one parentless session per board under the harness `poolhouse-ui`; the node names it like any
session, and it holds no grant of its own: when the node refuses it for want of one, the person's key (kept beside the
node, used only for a screen the person is sitting at) signs a grant for that one use. The token is kept in the
daemon's private client folder and never reaches a page.
"""

from __future__ import annotations

from pathlib import Path

from poolhouse import node_launch
from poolhouse.board import credentials, person_key, place, session as sessions
from poolhouse.board.client import Client, Denied

__all__ = ["HARNESS", "session"]

HARNESS = "poolhouse-ui"
MODEL = "person"
NATIVE_ID = "person"


def session(root: str | Path, client: Client | None = None, board: str = "") -> sessions.Session:
    """The person's session on ``board``, else on the board of the project at ``root``, registered the first time.

    `Denied` when ``root`` is not part of a project the node can place.
    """
    state = node_launch.default_state()
    node = client or Client(state, person=person_key.provider(state, attended=True))
    board = place.resolve(node, Path(root), board)
    try:
        name = str(node.call("session_lookup", board, harness=HARNESS, session=NATIVE_ID)["name"])
    except Denied:
        name = ""
    if name:
        try:
            return sessions.Session(node, board, credentials.load(node.state, board, name), name)
        except Denied:
            pass
    made = sessions.register(node, board, sessions.Native(MODEL, HARNESS, NATIVE_ID))
    return sessions.Session(node, board, made.token, made.name)

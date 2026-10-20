"""The person's own session on a board, for the UI daemon that acts for the person (docs/node.md, "The person").

The daemon registers one parentless session per board under the harness `poolhouse-ui`, one for each terminal or program
that asks (the node spends a grant only for the lineage that registered a token); the node names it like any
session, and it holds no grant of its own: when the node refuses it for want of one, the person's key (kept beside the
node) is asked to sign a grant for that one use, which needs a typed yes on a terminal, so a daemon with none signs nothing.
The token is kept in the daemon's private client folder and never reaches a page.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from poolhouse import node_launch
from poolhouse.board import credentials, person_key, place, session as sessions
from poolhouse.board.client import Client, Denied
from poolhouse.board.terminal import terminal_id

__all__ = ["HARNESS", "acting", "native_id", "on", "session"]

HARNESS = "poolhouse-ui"
MODEL = "person"
NATIVE_ID = "person"


def native_id() -> str:
    """The id of the person's session in the terminal or program this runs in."""
    return f"{NATIVE_ID}-{terminal_id()}"


def session(root: str | Path, client: Client | None = None, board: str = "") -> sessions.Session:
    """The person's session on ``board``, else on the board of the project at ``root``, registered the first time.

    `Denied` when ``root`` is not part of a project the node can place.
    """
    state = node_launch.default_state()
    node = client or Client(state, person=person_key.provider(state, attended=True))
    return on(node, place.resolve(node, Path(root), board))


def acting(state: Path) -> Callable[[str], tuple[str, str]]:
    """What a command a person runs at a terminal acts as when the person's key allows what it was refused: the name and token of the
    person's session of this terminal on a board."""
    def on_board(board: str) -> tuple[str, str]:
        held = on(Client(state), board)
        return held.name, held.token
    return on_board


def on(node: Client, board: str) -> sessions.Session:
    """The person's session of this terminal on ``board``, registered the first time."""
    try:
        name = str(node.call("session_lookup", board, harness=HARNESS, session=native_id())["name"])
    except Denied:
        name = ""
    if name:
        try:
            return sessions.Session(node, board, credentials.load(node.state, board, name), name)
        except Denied:
            pass
    made = sessions.register(node, board, sessions.Native(MODEL, HARNESS, native_id()), person=True)
    return sessions.Session(node, board, made.token, made.name)

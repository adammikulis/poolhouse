"""Read-only project attribution for optional workspace notifications."""
from __future__ import annotations

from pathlib import Path

from poolhouse.board.client import Client, Denied

NOT_PART = 'this directory is not part of a project: register it with project_add'


def existing_board(node: Client, cwd: Path) -> str:
    """The existing project of this directory; never select an ambient board or create a project."""
    try:
        board = node.call('project_resolve', path=str(cwd.resolve()))['board']
        if not isinstance(board, str) or not board:
            raise ValueError('notification project lookup returned an invalid board')
        return board
    except Denied as error:
        if str(error) in (NOT_PART, f'denied: {NOT_PART}') and not error.grant:
            return ''
        raise

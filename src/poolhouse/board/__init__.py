"""The client of the node: sockets, tokens and the board methods, for the CLI, the hooks and agents.

This package is also the public ``ph.board`` (docs/api.md): calling it, ``ph.board(...)``, is `connect`.
"""

from __future__ import annotations

import sys
import types
from typing import Any

from poolhouse.board.public import (
    Agent,
    Approval,
    Ask,
    Board,
    Claim,
    Message,
    Note,
    Task,
    connect,
    register,
)

__all__ = ["Agent", "Approval", "Ask", "Board", "Claim", "Message", "Note", "Task", "connect", "register"]


class _CallableBoard(types.ModuleType):
    """The package as the public entry: ``ph.board(agent=...)`` connects."""

    def __call__(self, **where: Any) -> Board:
        return connect(**where)


sys.modules[__name__].__class__ = _CallableBoard

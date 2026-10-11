"""The shape of a session's name on a board: what a client accepts as one before it asks the node about it."""

from __future__ import annotations

import re

__all__ = ["RESERVED", "valid_id", "valid_name"]

NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,47}$")
RESERVED = frozenset({"*", "all", "everyone", "workspace", "system", "human", "owner-token"})


def valid_name(name: str) -> bool:
    """Whether ``name`` can be a session's name."""
    return bool(NAME.match(name)) and name not in RESERVED and ".." not in name


def valid_id(name: str) -> bool:
    """Whether ``name`` is a session's name, or a ``parent/child`` name."""
    head, slash, tail = name.partition("/")
    return valid_name(head) and (not slash or valid_name(tail))

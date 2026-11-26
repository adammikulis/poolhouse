"""Ending a server of this machine."""

from __future__ import annotations

from typing import Any

from poolhouse import serverkeys
from poolhouse.serve import exit_guard
from poolhouse.serve.children import Children, Exit

__all__ = ["stop_server"]


def stop_server(children: Children, port: int, pid: int | None, *, proc: Any = None, grace_s: float = 5.0) -> list[Exit]:
    """End the server on ``port`` and everything of it that is left: its process tree (waited on when ``proc`` is ours), its
    exit guard and its API key with the file it was handed in. The one way a server of this machine is stopped, the pool's
    broker's included."""
    ended = children.end(port, pid, proc=proc, grace_s=grace_s)
    exit_guard.release(pid)
    serverkeys.forget(port)
    return ended

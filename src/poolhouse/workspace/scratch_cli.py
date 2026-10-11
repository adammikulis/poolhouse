"""The scratch-folder commands of `poolhouse-workspace`: private working space the node keeps for a session."""

from __future__ import annotations

import argparse
from typing import Any

from poolhouse.board.session import Session
from poolhouse.command import flag
from poolhouse.workspace import agent_filter, inbound, limits
from poolhouse.workspace.cli_options import FOR_AGENT

__all__ = ["TABLE"]


def _owner(args: argparse.Namespace, s: Session) -> str:
    """The session whose folders ``--for-agent`` names, by its name or an unambiguous start of it; empty for the caller's own."""
    named = getattr(args, "for_agent", "")
    return agent_filter.resolve(list(inbound.names(s)), named) if named else ""


def made(s: Session, name: str, ttl_s: int = 0) -> dict[str, str]:
    """Make a scratch folder that lasts ``ttl_s`` seconds (the owner's default when 0); its path."""
    return {"path": s.scratch_new(name, ttl_s or int(limits.load(limits.root()).scratch_ttl_s))}


def new(args: argparse.Namespace, s: Session) -> Any:
    """Make a scratch folder."""
    return made(s, args.name, int(args.ttl_hours * 3600))


def listing(args: argparse.Namespace, s: Session) -> Any:
    """Scratch folders with size and expiry."""
    return [{**{k: v for k, v in row.items() if k != "expires_ms"}, "expires": row["expires_ms"] / 1000}
            for row in s.scratch_ls(_owner(args, s))]


def path(args: argparse.Namespace, s: Session) -> Any:
    """A path inside a folder, refused if it escapes."""
    return {"path": s.scratch_path(args.name, args.relative, _owner(args, s))}


def remove(args: argparse.Namespace, s: Session) -> Any:
    """Delete a scratch folder."""
    return {"removed": s.scratch_rm(args.name, _owner(args, s))}


TABLE = (
    ("scratch-new", "make a scratch folder", [flag("name"), flag("--ttl-hours", type=float, default=0.0)], new),
    ("scratch-ls", "scratch folders with size and expiry", [FOR_AGENT], listing),
    ("scratch-path", "a path inside a folder, refused if it escapes", [flag("name"), flag("relative", nargs="?", default=""), FOR_AGENT], path),
    ("scratch-rm", "delete a scratch folder", [flag("name"), FOR_AGENT], remove),
)

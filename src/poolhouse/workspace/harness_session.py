"""A coding harness's session on a board and its lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path

from poolhouse import node_launch
from poolhouse.board import credentials, place
from poolhouse.board.client import Client, NodeError
from poolhouse.board.session import Session
from poolhouse.client.health import reported_models
from poolhouse.errors import Denied
from poolhouse.workspace import worktree_lifecycle

__all__ = ["BoardUnavailable", "HarnessSession", "session_of"]


class BoardUnavailable(Denied):
    """The board could not be reached or authenticated: local work carries on with a warning."""


def session_of(actor, cwd):
    """The session ``actor`` holds on the board of ``cwd``'s project. BoardUnavailable when the node or the token is not there;
    Denied when the node refuses the token or it belongs to another session."""
    try:
        node = Client(node_launch.default_state())
        board = place.resolve(node, Path(cwd))
        token = credentials.load(node.state, board, actor)
    except (NodeError, OSError) as error:
        raise BoardUnavailable(f'the board of {Path(cwd).name} is not reachable as {actor}: {error}') from error
    s = Session(node, board, token)
    try:
        named = s.name
    except NodeError as error:
        raise Denied(f'the board refused the token of {actor}: {error}') from error
    except OSError as error:
        raise BoardUnavailable(f'the board of {Path(cwd).name} is not reachable as {actor}: {error}') from error
    if named != actor:
        raise Denied(f'the token kept for {actor} belongs to {named}; ownership requires the launcher-bound identity')
    return s


@dataclass(slots=True)
class HarnessSession:
    """How a harness appears on a board: ``name`` is its session, ``parent`` the session that started it and that
    retires it when ``minted`` says the launcher made it, ``base`` the folder its checkouts are recorded in."""

    name: str
    board: str = ""
    minted: bool = False
    base: Path | None = None
    parent: Session | None = None
    managed_inbox: bool = False
    persistent: bool = False
    state: Path | None = None

    def flags(self) -> list[str]:
        """The workspace command flags this session's messages carry."""
        return ["--agent", self.name]

    def session(self) -> Session:
        """The session's own token on its board."""
        node = Client(self.state or node_launch.default_state())
        return Session(node, self.board, credentials.load(node.state, self.board, self.name), self.name)

    def record_model(self, alias: str, harness: str, server: str = "") -> bool:
        """Record a connected agent's model as claimed; False on an inactive session or refusal."""
        if not self.minted and not self.persistent:
            return False
        try:
            if server and alias not in reported_models(server):
                return False
            self.session().whoami(Path(alias).name if server else alias, harness)
        except (Denied, NodeError, ValueError, OSError, HTTPException):
            return False
        return True

    def pending_worktrees(self) -> list[dict]:
        """Return this session identity's unfinished coding scopes."""
        return worktree_lifecycle.pending(self.base, self.name) if self.base else []

    def require_clean(self) -> None:
        """Refuse a successful session exit with unfinished attributed checkouts."""
        if self.base:
            try:
                worktree_lifecycle.require_clean(self.base, self.name)
            except Denied as error:
                raise ValueError(str(error)) from error

    def revoke(self) -> bool:
        """End the session the launcher made and delete its token file; False when nothing was minted."""
        if not self.minted or self.parent is None:
            return False
        try:
            self.parent.retire(self.name)
        except (NodeError, OSError):
            return False
        finally:
            credentials.forget(self.parent.client.state, self.board, self.name)
        return True

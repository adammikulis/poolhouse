"""Which board a client is on: the project the directory it runs in belongs to."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import subprocess
from pathlib import Path

from poolhouse import home
from poolhouse.board.client import Client, Denied, Invalid
from poolhouse.board.terminal import terminal_id

__all__ = ["BOARD_ENV", "PLACER", "add", "at_path", "board_id", "existing", "find", "remove", "resolve"]

BOARD_ENV = "POOLHOUSE_BOARD"
PLACER = "poolhouse-place"
"""The harness of the session that adds the project of a board: the node takes a project from a session of its own board."""


def _git(path: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def board_id(path: Path) -> tuple[str, Path] | None:
    """``(board id, main working tree)`` of the repository ``path`` is in, or None. Every worktree
    of one repository gives the same id: its name and a few hex of its git common directory."""
    common = _git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return None
    common_dir = Path(common).resolve()
    tree = common_dir.parent if common_dir.name == ".git" else common_dir
    word = re.sub(r"[^a-z0-9]+", "-", tree.name.lower()).strip("-")[:30] or "project"
    return f"{word}-{hashlib.sha256(str(common_dir).encode()).hexdigest()[:6]}", tree


def at_path(client: Client, path: Path) -> str:
    """Read the actual registered project of a path, ignoring every board selector."""
    return str(client.call("project_resolve", path=str(path.resolve()))["board"])


def existing(client: Client, path: Path | None = None, named: str = "") -> str:
    """Select an existing board or resolve a known project, without registering either a project or a session.

    Explicit names and the environment select a location, never authority; the caller's credentials still decide access.
    """
    explicit = named or os.environ.get(BOARD_ENV, "")
    if explicit:
        return explicit
    return at_path(client, path or Path.cwd())


def resolve(client: Client, path: Path | None = None, named: str = "") -> str:
    """The board for ``named``, else $POOLHOUSE_BOARD, else the project of ``path`` (default the
    working directory); a repository the node does not know yet is added as a project."""
    explicit = named or os.environ.get(BOARD_ENV, "")
    if explicit:
        return explicit
    where = (path or Path.cwd()).resolve()
    try:
        return str(client.call("project_resolve", path=str(where))["board"])
    except Denied:
        if board_id(where) is None:
            raise
    return add(client, where)


def _placer(client: Client, board: str) -> str:
    """The token of the session that places a project on ``board``: the node takes a project from a session of its own board and asks nobody."""
    return str(client.call("register", board, "", model="", harness=PLACER, session=f"place-{terminal_id()}")["token"])


def add(client: Client, where: Path) -> str:
    """Add the git repository ``where`` is in as a project of the node (a worktree of one the node knows resolves to it already) and return its
    board, whatever the environment names. The node's reason is raised when it does not take the place (`Invalid`)."""
    found = board_id(where)
    if found is None:
        raise Denied("denied", "this directory is not in a git repository")
    board, tree = found
    try:
        client.call("project_add", "", _placer(client, board), id=board, kind="git", path=str(tree))
    except Invalid:
        with contextlib.suppress(Denied):
            return str(client.call("project_resolve", path=str(where))["board"])
        raise
    return str(client.call("project_resolve", path=str(where))["board"])


def find(client: Client, target: str) -> str:
    """The board of the registered project ``target`` names: its id, or a folder that is one of its places (one that no longer exists too);
    `Denied` when the node holds none."""
    held = {str(row["id"]): [str(source["path"]) for source in row["sources"]] for row in client.call("project_list")}
    if target in held:
        return target
    named = home.expand(target).resolve()
    with contextlib.suppress(Denied):
        return at_path(client, named)
    for board, places in held.items():
        if str(named) in places:
            return board
    raise Denied("denied", "no registered project has that id or that folder")


def remove(client: Client, target: str) -> str:
    """Forget the registered project ``target`` names (`find`) and return its board; the project's places are free for another project."""
    board = find(client, target)
    client.call("project_remove", "", _placer(client, board), id=board)
    return board

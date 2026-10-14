"""What a client keeps for its sessions: a token file per board and name, and read cursors.

The node stores only the hash of a token; the token itself is in a file only its owner reads.
A cursor is the map the node returned for one question (the inbox, the announcements), so the
next read of that question returns only what is new.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from poolhouse import private_counters
from poolhouse.board.client import Denied
from poolhouse.files import writing
from poolhouse.private_path import problem, redirected, windows_mount
from poolhouse.sentinel import human
from poolhouse.windows_private import restrict, validate

__all__ = ["cursors", "forget", "load", "prepare", "private", "remember", "store"]


def _directory(path: Path) -> Path:
    made = False
    try:
        path.mkdir(parents=True, mode=0o700)
        made = True
    except FileExistsError:
        pass
    info = path.lstat()
    if redirected(info) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("private session directory must be a plain directory")
    if os.name == "nt" and made:
        restrict(path)
    why = problem(path)
    if why:
        raise ValueError(f"private session directory {why}")
    current = path.lstat()
    if redirected(current) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
        raise ValueError("private session directory changed while opening")
    return path


def prepare(state: Path) -> Path:
    """The client folder under the node's ``state``, made owner-only and registered with sentinel so no tool call names it;
    ValueError when it is a symlink or reparse point, sits inside a git work tree or on a Windows-mounted filesystem."""
    path = state / "client"
    human.protect(path)
    if os.name == "nt":
        validate(state)
        validate(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if redirected(info):
            raise ValueError(f"{path} is a symlink or Windows reparse point")
    real = path.resolve()
    repo = next((p for p in (real, *real.parents) if (p / ".git").exists()), None)
    if repo is not None:
        raise ValueError(f"{path} resolves into the git work tree {repo}; keep the node's state out of it")
    if windows_mount(path):
        raise ValueError(f"{path} is on a Windows-mounted filesystem; keep session tokens under the WSL home directory")
    if os.name != "nt":
        why = problem(state)
        if why not in {"", "missing"} and not why.startswith("mode "):
            raise ValueError(f"{state}: {why}")
    _directory(state)
    _directory(path)
    return path


def _folder(state: Path, board: str) -> Path:
    path = prepare(state) / board
    return _directory(path)


def private(state: Path, board: str, name: str) -> Path:
    """A folder ``name`` for what a client keeps for ``board`` that only its owner may enter, made if needed."""
    path = _folder(state, board) / name
    return _directory(path)


def store(state: Path, board: str, name: str, token: str, parent: str = "") -> Path:
    """Write ``token`` for ``name`` on ``board``, atomically and readable by this user only, and beside it the ``parent`` the
    node named for the session (empty for a main session)."""
    target = _folder(state, board) / f"{name}.token"
    for path, text in ((target, token), (target.with_suffix(".parent"), parent)):
        with writing(path) as tmp:
            tmp.write_text(text + "\n", encoding="utf-8")
            if os.name == "nt":
                restrict(tmp)
            else:
                tmp.chmod(0o600)
    return target


def load(state: Path, board: str, name: str) -> str:
    """``name``'s token on ``board``; `Denied` when there is none or its file is not private."""
    path = state / "client" / board / f"{name}.token"
    why = problem(path)
    if why:
        raise Denied("denied", f"no usable token for {name} on {board}: {path} {why}")
    return path.read_text(encoding="utf-8").strip()


def cursors(state: Path, board: str, name: str) -> dict[str, dict[str, int]]:
    """The saved cursors of ``name``, by question."""
    return private_counters.read(_folder(state, board) / f"{name}.cursors.json")


def remember(state: Path, board: str, name: str, question: str, cursor: dict[str, int]) -> None:
    """Keep ``cursor`` as the place ``name`` has read up to for ``question``."""
    target = _folder(state, board) / f"{name}.cursors.json"
    if not isinstance(question, str) or not question or not private_counters.valid(cursor):
        raise ValueError("read cursor must map origins to nonnegative integers")
    with private_counters.locked(target):
        saved = private_counters.read(target)
        previous = saved.get(question, {})
        saved[question] = {origin: max(previous.get(origin, 0), number)
                           for origin, number in {**previous, **cursor}.items()}
        private_counters.write(target, saved)



def forget(state: Path, board: str, name: str) -> None:
    """Delete the token file of a session whose token the node revoked."""
    (state / "client" / board / f"{name}.token").unlink(missing_ok=True)
    (state / "client" / board / f"{name}.parent").unlink(missing_ok=True)

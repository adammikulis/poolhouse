"""A toy project with a backlog, rule files and session cards, shared by the card tests."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from poolhouse import cards

BACKLOG = """# Handoff

## 1. Speed (active)

| Order | Item | Next | Where |
|---|---|---|---|
| 1 | Make the junk go away, one step at a time | Delete it | `junk.txt` |

## 5. Checks (paused)

| Item | Next | Where |
|---|---|---|
| A paused row about leftovers | Delete them | `left.txt` |
"""
JUNK = {"title": "Delete the junk", "row": "Make the junk go away", "task": "Delete junk.txt.", "write": '["junk.txt"]',
        "check": "test ! -e junk.txt"}


def card(root: Path, ident: str, **fields: object) -> Path:
    """Write card ``ident``: `JUNK` with ``fields`` over it, a text quoted and a ``write``, ``read``, ``after`` or ``creates`` taken as TOML."""
    made = {**JUNK, **fields}
    lines = []
    for name, value in made.items():
        literal = name in ("write", "read", "after", "creates") or not isinstance(value, str)
        shown = str(value).lower() if isinstance(value, bool) else value if literal else '"""' + str(value) + '"""'
        lines.append(f"{name} = {shown}")
    path = root / cards.FOLDER / f"{ident}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def git(root: Path, *words: str) -> str:
    """What git says of ``words`` in the toy repository ``root``, committing as an invented person."""
    done = subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgSign=false",
                           *words], check=True, capture_output=True, text=True)
    return done.stdout.strip()


def committed(root: Path) -> str:
    """Commit everything the toy repository at ``root`` holds, making it one first; the commit."""
    if not (root / ".git").exists():
        git(root, "init", "--quiet")
    git(root, "add", "--all", ".")
    git(root, "commit", "--quiet", "--allow-empty", "-m", "as it stands")
    return git(root, "rev-parse", "HEAD")


def work(root: Path, base: str, changes: dict[str, str | None]) -> str:
    """A commit on ``base``, made in a worktree of its own, that writes each of ``changes`` (None deletes the file); the commit."""
    with tempfile.TemporaryDirectory() as held:
        tree = Path(held) / "tree"
        git(root, "worktree", "add", "--quiet", "--detach", str(tree), base)
        for name, text in changes.items():
            if text is None:
                (tree / name).unlink()
            else:
                (tree / name).parent.mkdir(parents=True, exist_ok=True)
                (tree / name).write_text(text, encoding="utf-8")
        made = committed(tree)
        git(root, "worktree", "remove", "--force", str(tree))
    return made


def project(root: Path) -> Path:
    """A project at ``root``: the backlog, a rule of each kind a brief quotes, and the files the cards name."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "HANDOFF.md").write_text(BACKLOG, encoding="utf-8")
    for name in dict.fromkeys(name for name, _start in cards.RULES):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("".join(f"{start} (as this project says it).\n" for each, start in cards.RULES if each == name), encoding="utf-8")
    (root / "junk.txt").write_text("junk\n", encoding="utf-8")
    (root / "left.txt").write_text("left\n", encoding="utf-8")
    return root

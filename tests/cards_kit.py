"""A toy project with a backlog, rule files and session cards, shared by the card tests."""

from __future__ import annotations

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

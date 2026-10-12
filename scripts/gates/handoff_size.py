"""Lines of HANDOFF.md past the wall."""

from __future__ import annotations

from pathlib import Path

from . import Finding

NAME = "handoff-size"
OWNER = ""
INCREMENTAL = True
FILE = "HANDOFF.md"
WARN = 300
WALL = 500


def describe() -> str:
    return (f"A line of {FILE} past line {WALL}; it lists what is pending and nothing else. Delete the entry "
            f"of every piece of work you finish and move nothing into it. A note appears from line {WARN}.")


def _lines(root: Path) -> int:
    path = root / FILE
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines()) if path.is_file() else 0


def find(root: Path) -> list[Finding]:
    return [Finding(FILE, n, "past the wall") for n in range(WALL + 1, _lines(root) + 1)]


def notes(root: Path) -> list[str]:
    count = _lines(root)
    if count < WARN:
        return []
    return [f"{FILE} is {count} lines: the wall is {WALL} and a warning starts at {WARN}; prune finished entries."]

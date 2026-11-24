"""Lines of HANDOFF.md past the wall: past a line count, a word count or a character count."""

from __future__ import annotations

from pathlib import Path

from . import Finding

NAME = "handoff-size"
OWNER = ""
INCREMENTAL = True
FILE = "HANDOFF.md"
WARN = 300
WALL = 500
WARN_WORDS = 2000
WALL_WORDS = 3000
WALL_CHARS = 24000


def describe() -> str:
    return (f"A line of {FILE} past line {WALL}, past {WALL_WORDS} words or past {WALL_CHARS} characters counted from the top; "
            f"it lists what is pending and nothing else. Delete the entry of every piece of work you finish and move "
            f"nothing into it. A note appears from line {WARN} or {WARN_WORDS} words.")


def _text(root: Path) -> list[str]:
    path = root / FILE
    return path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []


def _words(lines: list[str]) -> int:
    return sum(len(line.split()) for line in lines)


def find(root: Path) -> list[Finding]:
    found, words, chars = [], 0, 0
    for number, line in enumerate(_text(root), start=1):
        words += len(line.split())
        chars += len(line) + 1
        if number > WALL or words > WALL_WORDS or chars > WALL_CHARS:
            found.append(Finding(FILE, number, "past the wall"))
    return found


def notes(root: Path) -> list[str]:
    lines = _text(root)
    words = _words(lines)
    if len(lines) < WARN and words < WARN_WORDS:
        return []
    return [f"{FILE} is {len(lines)} lines and {words} words: the wall is {WALL} lines, {WALL_WORDS} words or {WALL_CHARS} characters, "
            f"and a warning starts at {WARN} lines or {WARN_WORDS} words; prune finished entries."]

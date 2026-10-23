"""The grammar of a unit id, and the plain form of text that came from a command."""

from __future__ import annotations

import re
import shutil
import unicodedata
from collections.abc import Iterable

__all__ = ["BANNED", "plain", "refusal"]

BANNED = frozenset({"Cc", "Cf", "Zl", "Zp", "Co", "Cs", "Cn"})
"""The Unicode general categories no id and no printed text holds."""

_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


def refusal(text: object, refuse_prefix: Iterable[str] = ()) -> str | None:
    """The reason ``text`` is not an id, or None when it is one."""
    if not isinstance(text, str) or not text:
        return "an id is at least one character of text"
    if not unicodedata.is_normalized("NFC", text):
        return "an id is Unicode normal form C"
    for char in text:
        category = unicodedata.category(char)
        if category in BANNED:
            return f"an id holds no control, format, separator or private-use character (U+{ord(char):04X})"
    if text.startswith("-"):
        return "an id does not begin with '-'"
    for prefix in refuse_prefix:
        if text.startswith(prefix):
            return f"an id does not begin with {prefix!r} (run.refuse_prefix)"
    return None


def plain(text: str, *, width: int | None = None) -> str:
    """``text`` as data for a person: escapes, carriage-return overwrites and invisible characters removed, each line cut to ``width``
    characters (the terminal's width when None)."""
    columns = width if width is not None else shutil.get_terminal_size().columns
    lines = []
    for line in _ESCAPES.sub("", text).replace("\r\n", "\n").split("\n"):
        line = line.rsplit("\r", 1)[-1]
        kept = "".join(char for char in line if char == "\t" or unicodedata.category(char) not in BANNED)
        lines.append(kept if len(kept) <= columns else kept[:columns - 1] + "…")
    return "\n".join(lines)

"""Text from outside as one safe printable line."""

from __future__ import annotations

import unicodedata
from typing import Any

__all__ = ["escape", "utf8"]

_EDGE = "…"


def escape(text: str, limit: int = 60) -> str:
    """``text`` as one line of at most ``limit`` characters: every control, format (bidi,
    zero-width), separator, private or unassigned character written as a visible escape such
    as ``\\x1b`` or ``\\u202e``, cut with an ellipsis when long."""
    out = "".join(ch if _plain(ch) else _escape(ch) for ch in text)
    return out if len(out) <= limit else out[:limit - 1] + _EDGE


def utf8(value: Any) -> Any:
    """``value`` (text, or the text of the lists and mappings in it) with each lone surrogate, which no UTF-8 file or message can hold, written as
    U+FFFD, as a JavaScript program writes it."""
    if isinstance(value, str):
        return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    if isinstance(value, dict):
        return {utf8(key): utf8(each) for key, each in value.items()}
    return [utf8(each) for each in value] if isinstance(value, (list, tuple)) else value


def _plain(ch: str) -> bool:
    return ch == " " or (ch.isprintable() and unicodedata.category(ch)[0] not in "CZ")


def _escape(ch: str) -> str:
    named = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
    if ch in named:
        return named[ch]
    point = ord(ch)
    if point < 0x100:
        return f"\\x{point:02x}"
    return f"\\u{point:04x}" if point < 0x10000 else f"\\U{point:08x}"

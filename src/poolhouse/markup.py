"""A page's text, out of its markup: readable words with the tags gone, and a cut that
ends on a sentence rather than mid-word."""

from __future__ import annotations

import contextlib
import re
from html import unescape
from html.parser import HTMLParser
from typing import Any

__all__ = ["cut", "extract"]


class _Stripper(HTMLParser):
    """The words of a page, without its scripts, styles or tags. The fallback reader."""

    SKIP = {"script", "style", "noscript", "template", "svg"}
    VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param",
                      "source", "track", "wbr"})
    DISPLAY_NONE = re.compile(r"(?:^|;)\s*display\s*:\s*none\s*(?:!\s*important\s*)?(?:;|$)", re.I)
    BREAK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section",
             "article", "header", "footer", "nav", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__()
        self.title: list[str] = []
        self.parts: list[str] = []
        self._skip = 0
        self._stack: list[tuple[str, bool]] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        style = dict(attrs).get("style") or ""
        style = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
        hidden = tag in self.SKIP or bool(self.DISPLAY_NONE.search(style))
        if tag not in self.VOID:
            self._stack.append((tag, hidden))
            self._skip += int(hidden)
        if self._skip or hidden:
            return
        if tag == "title":
            self._in_title = True
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        matching = next((i for i in range(len(self._stack) - 1, -1, -1)
                         if self._stack[i][0] == tag), None)
        if matching is None:
            return
        for closed, hidden in self._stack[matching:]:
            self._skip -= int(hidden)
            if closed == "title":
                self._in_title = False
        del self._stack[matching:]
        if tag in self.BREAK and not self._skip:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
        else:
            self.parts.append(data)


def extract(html: str, url: str = "") -> tuple[str, str]:
    """``(title, text)`` read out of a page: trafilatura when installed, tags stripped when not."""
    try:
        import trafilatura
    except ImportError:
        trafilatura = None
    title, text = "", ""
    if trafilatura is not None:
        with contextlib.suppress(Exception):
            doc = trafilatura.bare_extraction(html, url=url or None, with_metadata=True)
            if doc is not None:
                title, text = str(doc.title or ""), str(doc.text or "")
    if not text:
        stripper = _Stripper()
        with contextlib.suppress(Exception):
            stripper.feed(html)
        title = title or " ".join(unescape("".join(stripper.title)).split())
        lines = [" ".join(unescape(line).split()) for line in "".join(stripper.parts).split("\n")]
        text = "\n".join(line for line in lines if line)
    return title.strip(), text.strip()


_SENTENCE_END = re.compile(r"[.!?…]['\")\]]?(?=\s)|\n")


def cut(text: str, limit: int) -> tuple[str, bool]:
    """``text`` no longer than ``limit``, ended at a sentence when one is near enough.

    A page cut mid-word reads as broken; a page cut mid-sentence reads as a claim that was
    never made. So the cut goes back to the last sentence end in the second half of the
    window, then to the last space, and only then to the character.
    """
    if len(text) <= limit:
        return text, False
    window = text[:limit]
    ends = [m.end() for m in _SENTENCE_END.finditer(window)]
    at = ends[-1] if ends and ends[-1] >= limit // 2 else 0
    if not at:
        space = window.rfind(" ")
        at = space if space >= limit // 2 else limit
    return window[:at].rstrip(), True

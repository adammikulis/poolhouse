"""What the model bridge sees of a session's prompts: whether each request continues what the model server has read, and how far the server is in
reading the one it has.

A model server keeps what it has read of a conversation and reads only what a request adds to it. A request that changes anything earlier (another
tool list, an edited or dropped message, a summary asked with another prompt) is read again from where it differs, which on a long conversation takes
many minutes; a model with recurrent state goes back further still, to a point it saved. `Watch.sent` compares the tools, the system prompt and the
messages of each request, in the order a chat template renders them, with the conversations seen before, and says where one rewrites. It keeps
digests, counts, tool names and roles, never text. `progress` reads the counters of the server's ``/slots``.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["COMPACTION", "REPORT_CHARS", "Look", "Progress", "Seen", "Watch", "progress"]

REPORT_CHARS = 32_000
"""The characters (about 8,000 tokens) a rewrite must make the server read again, and give up of a conversation it held, to be said."""
KEPT = 4
"""The conversations one session is compared with: its own, and those of a title, a summary or a second agent beside it."""
COMPACTION = ("You MUST summarize the conversation above into a structured summary",
              "Update the existing checkpoint in the conversation above into one consolidated summary")
"""How the pinned OpenCode (`opencode_install.VERSION`) begins the message that asks for a compaction's summary; tests/test_opencode_pinned.py runs
that release into one."""
BILLING = re.compile(r"(x-anthropic-billing-header:[^\n]*?cch=)[0-9a-f]{5};")
"""A stamp Claude Code puts at the head of its system prompt that differs on every request; llama-server sets it to a constant before it reads it."""
NAME = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


@dataclass(frozen=True, slots=True)
class _Part:
    """One piece of a prompt as a template renders it: a tool, the system prompt or a message; ``name`` is a tool's name or a message's role."""

    kind: str
    name: str
    digest: bytes
    chars: int


@dataclass(frozen=True, slots=True)
class Seen:
    """What one request is: whether it asks for a compaction's summary and, when it rewrites history, the sentence that says so."""

    compaction: bool = False
    rewrite: str = ""


@dataclass(frozen=True, slots=True)
class Progress:
    """The server's counters for the one request it is at work on: the tokens of the conversation it holds (``read``), those it took from its cache
    and those it has written; it is reading while it has written none."""

    read: int
    cached: int
    written: int


@dataclass(frozen=True, slots=True)
class Look:
    """What a bridge knows now: whether every request of its session still waits for a slot, whether one asks for a compaction's summary, how far
    the server is (None while that is not known) and every rewrite it has seen, oldest first."""

    waiting: bool = False
    compacting: bool = False
    progress: Progress | None = None
    rewrites: tuple[str, ...] = ()


def _plain(value: Any) -> Any:
    """``value`` without what a harness moves between requests and no template renders: cache markers, and a list of text parts as one text."""
    if isinstance(value, Mapping):
        return {key: _plain(each) for key, each in value.items() if key != "cache_control"}
    if isinstance(value, list):
        found = [_plain(each) for each in value]
        if found and all(isinstance(each, dict) and each.get("type") == "text" and set(each) == {"type", "text"} for each in found):
            return "".join(str(each["text"]) for each in found)
        return found
    return BILLING.sub(r"\g<1>00000;", value) if isinstance(value, str) else value


def _part(kind: str, name: object, value: Any) -> _Part:
    text = json.dumps(_plain(value), ensure_ascii=False, separators=(",", ":"))
    shown = name if isinstance(name, str) and NAME.fullmatch(name) else ""
    return _Part(kind, shown, hashlib.sha256(text.encode("utf-8", "surrogatepass")).digest(), len(text))


def _tool_name(tool: Any) -> object:
    held = tool.get("function", tool) if isinstance(tool, Mapping) else {}
    return held.get("name") if isinstance(held, Mapping) else ""


def _parts(body: Mapping[str, Any]) -> tuple[_Part, ...]:
    """The prompt of a request of the chat, messages or responses route, in the order a template renders it: tools, system, messages."""
    tools = body.get("tools")
    found = [_part("tool", _tool_name(each), each) for each in (tools if isinstance(tools, list) else ())]
    for key in ("system", "instructions"):
        if body.get(key):
            found.append(_part("system", "", body[key]))
    said = body.get("messages", body.get("input"))
    for each in said if isinstance(said, list) else ([said] if said else []):
        found.append(_part("message", each.get("role") if isinstance(each, Mapping) else "", each))
    return tuple(found)


def _compaction(body: Mapping[str, Any]) -> bool:
    said = body.get("messages", body.get("input"))
    last = said[-1] if isinstance(said, list) and said else None
    if not isinstance(last, Mapping) or last.get("role") != "user":
        return False
    text = _plain(last.get("content"))
    return isinstance(text, str) and text.startswith(COMPACTION)


def _chars(parts: tuple[_Part, ...]) -> int:
    return sum(each.chars for each in parts)


def _common(held: tuple[_Part, ...], new: tuple[_Part, ...]) -> int:
    """The leading parts both have."""
    count = 0
    while count < min(len(held), len(new)) and held[count].digest == new[count].digest:
        count += 1
    return count


def _where(held: tuple[_Part, ...], new: tuple[_Part, ...], at: int) -> str:
    """The place ``new`` first differs from ``held``, by its kind, number, and tool name or role."""
    was = held[at]
    if at >= len(new):
        return f"message {sum(each.kind == 'message' for each in held[:at]) + 1}, which is gone"
    now = new[at]
    number = sum(each.kind == now.kind for each in new[:at]) + 1
    if now.kind == "tool":
        before = f" where {was.name} was" if was.kind == "tool" and was.name != now.name else ""
        return f"tool {number} ({now.name or 'unnamed'}{before})"
    return "the system prompt" if now.kind == "system" else f"message {number} ({now.name or 'no role'})"


class Watch:
    """The conversations of one session as the server was asked them."""

    def __init__(self) -> None:
        self._held: list[tuple[_Part, ...]] = []
        self._lock = threading.Lock()

    def sent(self, route: str, body: bytes) -> Seen:
        """Take in the request about to be sent on ``route``; one that holds no prompt (a token count, say) says nothing."""
        if route.endswith("count_tokens"):
            return Seen()
        try:
            found = json.loads(body)
        except (ValueError, RecursionError):
            return Seen()
        new = _parts(found) if isinstance(found, dict) else ()
        if not new:
            return Seen()
        compaction = _compaction(found)
        with self._lock:
            shared = [(_common(held, new), held) for held in self._held]
            grown = [held for count, held in shared if count == len(held)]
            if grown:
                self._held = [new, *(held for held in self._held if held not in grown)]
                return Seen(compaction)
            said = ""
            if shared:
                count, best = max(shared, key=lambda each: _chars(each[1][: each[0]]))
                kept = _chars(best[:count])
                largest = max(self._held, key=_chars)
                if min(_chars(largest), _chars(new)) - kept >= REPORT_CHARS:
                    against = best if kept else largest
                    said = (f"a request rewrote history the model server had read: it differs at {_where(against, new, count if kept else 0)}; "
                            f"{kept:,} of its {_chars(new):,} characters match, so about {(_chars(new) - kept) // 4:,} tokens are read again")
                if kept * 2 >= _chars(best):   # the same conversation, changed: what it was is not compared with again
                    self._held.remove(best)
            self._held.insert(0, new)
            while len(self._held) > KEPT:
                self._held.remove(min(self._held, key=_chars))
            return Seen(compaction, said)


def _count(held: Mapping[str, Any], key: str) -> int:
    value = held.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**12 else 0


def progress(slots: object) -> Progress | None:
    """The counters of the one slot at work in llama-server's ``/slots`` reply; None when none or several are, or it reports none. Nothing else of
    the reply is kept: it can hold the prompt's own text."""
    busy = [each for each in slots if isinstance(each, Mapping) and each.get("is_processing") is True] if isinstance(slots, list) else []
    if len(busy) != 1:
        return None
    token = busy[0].get("next_token")
    token = token[0] if isinstance(token, list) and token else token
    written = _count(token, "n_decoded") if isinstance(token, Mapping) else 0
    read, cached = _count(busy[0], "n_prompt_tokens"), _count(busy[0], "n_prompt_tokens_cache")
    return Progress(read, cached, written) if read or written else None

"""What the model bridge sees of a session's prompts: whether each request continues what the model server has read, and how far the server is in
reading the one it has.

A model server keeps what it has read of a conversation and reads only what a request adds to it. A request that changes anything earlier (another
tool list, an edited or dropped message, a summary asked with another prompt) is read again from where it differs, which on a long conversation takes
many minutes; a model with recurrent state goes back further still, to a point it saved. `Watch.sent` compares the tools, the system prompt and the
messages of each request, in the order a chat template renders them, with the conversations seen before, and says where one rewrites; the shorter
history a compaction leaves is no rewrite. It keeps digests, counts, tool names and roles, never text, and gives the bridge the ends of the newest
messages (`Seen.markers`) for as long as the request is with the server: `progress` finds the request's own slot by them in ``/slots``.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
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
CHECKPOINT = "<conversation-checkpoint>"
"""How the pinned OpenCode begins the one message that stands for a compacted history."""
MARKERS, MARKER_CHARS, MARKED = 3, 160, 24
"""The newest messages whose ends tell a request from another's, the characters of each end, and the characters they must come to."""
BILLING = re.compile(r"\A(x-anthropic-billing-header:[^\n]{0,200}?cch=)[0-9a-f]{5};")
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
    """What one request is: whether it asks for a compaction's summary, when it rewrites history the sentence that says so, and the ends of its
    newest messages that are not the model's (`progress`), which are text and kept no longer than the request."""

    compaction: bool = False
    rewrite: str = ""
    markers: tuple[str, ...] = field(default=(), repr=False)


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
    the server is (None while that is not known), every rewrite it has seen, oldest first, and the compactions it has been asked for."""

    waiting: bool = False
    compacting: bool = False
    progress: Progress | None = None
    rewrites: tuple[str, ...] = ()
    compactions: int = 0


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


def _texts(value: Any) -> Iterator[str]:
    """Every text a message holds: its content, its text parts and what its tool results carry."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for each in value:
            yield from _texts(each)
    elif isinstance(value, Mapping):
        for key in ("text", "content", "output"):
            if key in value:
                yield from _texts(value[key])


def _markers(body: Mapping[str, Any]) -> tuple[str, ...]:
    """The ends of the newest texts that a template renders as they are (no message of the model's, which it may wrap), too few to tell a request by
    left out."""
    said = body.get("messages", body.get("input"))
    found: list[str] = []
    for each in reversed(said if isinstance(said, list) else []):
        if isinstance(each, Mapping) and each.get("role") != "assistant":
            found += [text.strip()[-MARKER_CHARS:] for text in _texts(each) if text.strip()]
        if len(found) >= MARKERS:
            break
    kept = tuple(found[:MARKERS])
    return kept if sum(map(len, kept)) >= MARKED else ()


def _head(parts: tuple[_Part, ...]) -> tuple[bytes, ...]:
    """The tools and the system prompt of a prompt: what comes before its conversation."""
    found = []
    for each in parts:
        if each.kind == "message" and each.name not in ("system", "developer"):
            break
        found.append(each.digest)
    return tuple(found)


def _checkpointed(body: Mapping[str, Any]) -> bool:
    """Whether the conversation of a request opens with a compaction's checkpoint."""
    said = body.get("messages", body.get("input"))
    first = next((each for each in said if isinstance(each, Mapping) and each.get("role") not in ("system", "developer")), None) \
        if isinstance(said, list) else None
    text = _plain(first.get("content")) if first else None
    return isinstance(text, str) and text.startswith(CHECKPOINT)


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
    """The conversations of one session as the server was asked them: of one box, or of every box a continued conversation is held in."""

    def __init__(self) -> None:
        self._held: list[tuple[_Part, ...]] = []
        self._summarized: tuple[_Part, ...] = ()
        """The conversation a summary was last asked of, until the history that summary replaces has been seen."""
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
        compaction, markers = _compaction(found), _markers(found)
        with self._lock:
            before = self._summarized
            if compaction:
                self._summarized = new
            shared = [(_common(held, new), held) for held in self._held]
            grown = [held for count, held in shared if count == len(held)]
            if grown:
                self._held = [new, *(held for held in self._held if held not in grown)]
                return Seen(compaction, markers=markers)
            if before and _checkpointed(found) and _head(new) == _head(before):   # what the compaction leaves, read once as it must be
                self._summarized = ()
                self._held = [new, *(held for held in self._held if held != before)]
                return Seen(compaction, markers=markers)
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
            return Seen(compaction, said, markers)


def _count(held: Mapping[str, Any], key: str) -> int:
    value = held.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 10**12 else 0


def progress(slots: object, markers: tuple[str, ...]) -> Progress | None:
    """The counters of the slot of llama-server's ``/slots`` reply that is at work on the request ``markers`` (`Seen.markers`) come from; None when
    it cannot be told: no marker, a server that does not say a slot's prompt (it does when it is started to save slots), no slot or several that
    hold them. Another client's counts are never given for this session's. Nothing else of the reply is kept: it holds prompts' own text."""
    if not markers or not isinstance(slots, list):
        return None
    ours = [each for each in slots if isinstance(each, Mapping) and each.get("is_processing") is True and isinstance(each.get("prompt"), str)
            and all(marker in each["prompt"] for marker in markers)]
    if len(ours) != 1:
        return None
    token = ours[0].get("next_token")
    token = token[0] if isinstance(token, list) and token else token
    written = _count(token, "n_decoded") if isinstance(token, Mapping) else 0
    read, cached = _count(ours[0], "n_prompt_tokens"), _count(ours[0], "n_prompt_tokens_cache")
    return Progress(read, cached, written) if read or written else None

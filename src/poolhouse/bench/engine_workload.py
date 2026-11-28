"""The requests every engine is measured with: a growing conversation with planted facts, agent turns over real
files, and checks whose answers are known.

The text is the checkout's own source and documents, so two machines on one commit send the same bytes. A session is
one conversation that grows to each level in turn: at a level it reads the new text and answers about a fact planted in
it, writes a fixed amount, and, at the agent levels, runs tool turns over real files. The last turns ask for facts
planted at the start, middle and end.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poolhouse.bench.backends import timings_of

__all__ = ["Material", "Plan", "Turn", "call_matches", "corpus", "needle", "segments", "session", "tool_checks"]

SYSTEM = ("You are a careful software engineer. Answer from the text you are given. When a tool is offered and the "
          "answer is in a file, call the tool.")

_UNITS = ("harbour crane", "copper kiln", "meadow pump", "winter lantern", "granite press", "willow loom",
          "ferry winch", "orchard still", "compass lathe", "anvil hoist", "signal mill", "ledger vault")

#: What each write turn asks, taken in order, so every engine writes the same four things.
WRITES = (
    "Write a Python module that parses lines of the form 'KEY=VALUE' with comments and quoting into a dict, with "
    "type hints, error messages that name the line number, and five usage examples. Write the whole module.",
    "Rewrite the most repetitive function you have seen in the text above so that it is shorter, show the whole new "
    "function, then explain each change.",
    "Write a JSON document describing ten test cases for a rate limiter: name, input events with times, and the "
    "expected decisions. Write all ten in full.",
    "Explain in detail, with code excerpts from the text above, how one of the modules shown works from its entry "
    "point to its result.",
)

TOOLS = [
    {"type": "function", "function": {
        "name": "read_file", "description": "Read a file of the repository.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search", "description": "Find the lines of the repository's files holding a text.",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
]

#: Characters of a file or a search handed back as one tool result.
TOOL_CHARS = 6000


@dataclass(frozen=True)
class Plan:
    """What a session asks: the context levels in tokens, which of them run agent turns, the tokens a write turn
    writes, and the characters per token the segments are cut with."""

    levels: tuple[int, ...] = (4096, 32768, 131072, 204800)
    agent_levels: tuple[int, ...] = (32768, 131072)
    generate: int = 256
    chars_per_token: float = 3.6


@dataclass(frozen=True)
class Material:
    """What a session is made of and where its rows go: the checkout that answers tool calls, the sink for rows, the
    watcher opened around each request, the salt of the planted codes and, when frozen, the text cut into segments."""

    root: Path
    keep: Callable[[dict[str, Any]], None]
    watch: Callable[[], Any] | None = None
    salt: str = ""
    text: str = ""


@dataclass(frozen=True)
class Turn:
    """How one request is sent: what it is, the level it belongs to, its output cap and whether the model thinks."""

    kind: str
    level: int
    limit: int
    think: bool = False
    tools: bool = False


def corpus(root: Path, chars: int) -> str:
    """``chars`` characters of the checkout's Python sources and documents, in path order, code and prose in turn."""
    code = sorted((root / "src" / "poolhouse").rglob("*.py"), key=lambda p: p.relative_to(root).as_posix())
    prose = sorted((root / "docs").rglob("*.md"), key=lambda p: p.relative_to(root).as_posix())
    held: list[str] = []
    size = 0
    queue = [path for pair in zip(code, prose, strict=False) for path in pair] + code[len(prose):] + prose[len(code):]
    for path in queue:
        if size >= chars:
            break
        text = f"\n===== {path.relative_to(root).as_posix()} =====\n{path.read_text(encoding='utf-8')}"
        held.append(text)
        size += len(text)
    if size < chars:
        raise ValueError(f"the checkout holds {size} characters of text, under the {chars} asked")
    return "".join(held)[:chars]


def needle(index: int, salt: str = "") -> tuple[str, str, str]:
    """``(unit, code, sentence)`` of the fact planted in segment ``index``; the code is derived, never in the text."""
    unit = _UNITS[index % len(_UNITS)]
    digest = hashlib.sha256(f"{salt}:{index}".encode()).hexdigest()
    code = f"{chr(65 + int(digest[0], 16))}{chr(75 + int(digest[1], 16) % 15)}-{int(digest[2:8], 16) % 9000 + 1000}"
    return unit, code, f"\n# Maintenance record: the calibration code of the {unit} is {code}.\n"


def segments(text: str, plan: Plan, salt: str = "") -> list[str]:
    """``text`` cut into one segment per level, each holding that level's new text with its fact in the middle."""
    out, start, before = [], 0, 0
    for index, level in enumerate(plan.levels):
        length = int((level - before) * plan.chars_per_token)
        piece = text[start:start + length]
        if len(piece) < length:
            raise ValueError(f"the text ends before level {level}")
        middle = piece.find("\n", len(piece) // 2)
        middle = len(piece) // 2 if middle < 0 else middle
        out.append(piece[:middle] + needle(index, salt)[2] + piece[middle:])
        start, before = start + length, level
    return out


def _found(root: Path, call: Mapping[str, Any]) -> str:
    """The real result of a tool call over the checkout; a path outside it is answered as absent."""
    function = call.get("function") or {}
    try:
        given = function.get("arguments") or {}
        given = json.loads(given) if isinstance(given, str) else dict(given)
    except (ValueError, TypeError):
        return "error: the arguments are not a JSON object"
    if function.get("name") == "read_file":
        path = (root / str(given.get("path") or "")).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            return "error: no such file"
        return path.read_text(encoding="utf-8", errors="replace")[:TOOL_CHARS]
    wanted = str(given.get("text") or "")
    lines: list[str] = []
    for path in sorted((root / "src" / "poolhouse" / "bench").glob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if wanted and wanted in line:
                lines.append(f"{path.relative_to(root).as_posix()}:{number}: {line.strip()}")
    return "\n".join(lines)[:TOOL_CHARS] or "no match"


class _Talk:
    """One conversation sent turn by turn; every request becomes a row handed to ``keep``."""

    def __init__(self, ask: Callable[..., Any], keep: Callable[[dict[str, Any]], None],
                 watch: Callable[[], Any] | None) -> None:
        self.ask, self.keep, self.watch = ask, keep, watch
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}]

    def send(self, turn: Turn, content: str | None, **noted: Any) -> Any:
        if content is not None:
            self.messages.append({"role": "user", "content": content})
        first: list[float] = []
        span = self.watch() if self.watch else None
        began = time.perf_counter()
        reply = self.ask(self.messages, turn, lambda *_: first or first.append(time.perf_counter() - began))
        wall = time.perf_counter() - began
        watched = span.close() if span else {}
        timings = timings_of(reply)
        said = {"role": "assistant", "content": reply.content or ""}
        if reply.thinking:
            said["reasoning_content"] = reply.thinking
        if reply.tool_calls:
            said["tool_calls"] = reply.tool_calls
        self.messages.append(said)
        correct = noted.pop("expect", None)
        row = {"kind": turn.kind, "level": turn.level, "think": turn.think, "wall_s": round(wall, 3),
               "ttft_s": round(first[0], 4) if first else None, "finish": reply.finish_reason,
               **{key: timings.get(key) for key in ("prompt_n", "cache_n", "prompt_ms", "predicted_n",
                                                    "predicted_ms", "draft_n", "draft_n_accepted")},
               "text": (reply.content or "")[:4000], "tool_calls": reply.tool_calls, **watched, **noted}
        if correct is not None:
            row["expected"], row["correct"] = correct, correct in (reply.content or "")
        self.keep(row)
        return reply


def session(ask: Callable[..., Any], plan: Plan, material: Material) -> None:
    """One conversation through every level of ``plan``. ``ask(messages, turn, on_piece)`` sends one request and
    returns its reply; a new salt changes the planted codes and the first line, so no cache carries over."""
    root, salt = material.root, material.salt
    text = material.text or corpus(root, int((plan.levels[-1] + 1) * plan.chars_per_token))
    pieces = segments(text, plan, salt)
    talk = _Talk(ask, material.keep, material.watch)
    talk.messages[0]["content"] = f"Session {salt or 'one'}. {SYSTEM}"
    for index, (level, piece) in enumerate(zip(plan.levels, pieces, strict=True)):
        unit, code, _ = needle(index, salt)
        talk.send(Turn("read", level, 48), f"{piece}\n\nWhat is the calibration code of the {unit}? Answer with the "
                  "code only.", expect=code)
        talk.send(Turn("write", level, plan.generate), WRITES[index % len(WRITES)])
        if level not in plan.agent_levels:
            continue
        for step, think in enumerate((False, True)):
            reply = talk.send(Turn("agent-call", level, 768, think=think, tools=True),
                              ["Read the file src/poolhouse/bench/quiet.py and name the functions it defines.",
                               "Search for the text 'def residency' and say which file holds it."][step])
            calls = reply.tool_calls or []
            for call in calls:
                talk.messages.append({"role": "tool", "tool_call_id": call.get("id") or "call",
                                      "content": _found(root, call)})
            if calls:
                talk.send(Turn("agent-answer", level, 768, think=think, tools=True), None)
    last = len(plan.levels) - 1
    for index, depth in ((0, "start"), (last // 2, "middle"), (last, "end")):
        unit, code, _ = needle(index, salt)
        talk.send(Turn("recall", plan.levels[-1], 48), f"What is the calibration code of the {unit}? Answer with the "
                  "code only.", expect=code, depth=depth, planted_level=plan.levels[index])


def call_matches(calls: Sequence[Mapping[str, Any]] | None, expected: Mapping[str, Any]) -> bool:
    """Whether the first tool call names ``expected['name']`` with every argument of ``expected['args']``."""
    if not calls:
        return False
    function = calls[0].get("function") or {}
    try:
        given = function.get("arguments") or {}
        given = json.loads(given) if isinstance(given, str) else dict(given)
    except (ValueError, TypeError):
        return False

    def same(have: Any, want: Any) -> bool:
        if isinstance(want, str):
            return isinstance(have, str) and have.strip().casefold() == want.casefold()
        return type(have) is not str and have == want

    return function.get("name") == expected["name"] and all(
        same(given.get(key), want) for key, want in expected["args"].items())


def tool_checks(ask: Callable[..., Any], items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Each item asked once with its own tools: whether the first call is the expected one."""
    out = []
    for item in items:
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": item["ask"]}]
        turn = Turn("tool-check", 0, 768, tools=True)
        reply = ask(messages, turn, lambda *_: None, tools=item["tools"])
        out.append({"kind": "tool-check", "item": item["id"], "correct": call_matches(reply.tool_calls, item["expect"]),
                    "tool_calls": reply.tool_calls, "text": (reply.content or "")[:2000]})
    return out

"""``poolhouse-serve strata-proof MODEL``: the live proof of a Strata server on this device.

It leases the server from the broker, then measures reading and writing speed, long-context recall (a code word hidden at three
depths), a tool call and its answer, how much of a conversation is reused on the next turn and after another conversation, the
thinking switch, refusal of a picture named by file or address, and what drafts. Every figure is the server's own report of a
real reply. The record is written under the state root and the exit status is 0 only when every check passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poolhouse import home, serverkeys
from poolhouse.command import flag
from poolhouse.files import write_json
from poolhouse.http import Options, ServerError, request_json
from poolhouse.log import say, warn
from poolhouse.serve.backend import ServerSpec
from poolhouse.serve.contextsize import parse_context
from poolhouse.serve.manager import ServerManager

__all__ = ["CHECKS", "OPTIONS", "Proof", "cmd_prove", "filler"]

CHARS_PER_TOKEN = 4
"""How the filler is sized before the server counts it; the count reported is the server's."""
DEPTHS = (0.1, 0.5, 0.9)
LENGTHS = (4_000, 32_000, 128_000, 245_000)
"""Prompt sizes in tokens; the last leaves room for a reply in 262,144."""
REPLY_S = 3600.0
#: share of the earlier turn a following turn must reuse rather than read again
REUSED = 0.9
WORDS = ("lantern", "orchard", "copper", "meadow", "harbor", "quarry", "willow", "ember", "granite", "thistle", "saddle", "beacon")
TOOL = {"type": "function", "function": {"name": "read_ledger", "description": "Read one account's balance from the ledger.",
                                          "parameters": {"type": "object", "required": ["account"], "properties": {
                                              "account": {"type": "string"}}}}}


def filler(tokens: int, seed: int) -> list[str]:
    """Numbered lines of invented records, about ``tokens`` tokens of them, the same for the same seed."""
    lines, size = [], 0
    while size < tokens * CHARS_PER_TOKEN:
        first, second, shelf = _drawn(seed, len(lines))
        line = f"Record {len(lines) + 1}: the {WORDS[first]} {WORDS[second]} was logged at shelf {shelf}."
        lines.append(line)
        size += len(line) + 1
    return lines


def _drawn(seed: int, row: int) -> tuple[int, int, int]:
    """Two word numbers and a shelf for one row, fixed by ``seed`` and ``row``."""
    digest = hashlib.sha256(f"{seed}:{row}".encode()).digest()
    return digest[0] % len(WORDS), digest[1] % len(WORDS), int.from_bytes(digest[2:4], "big") % 1000


@dataclass
class Proof:
    """One server under proof and the record of what it did."""

    base_url: str
    key: str
    rows: list[dict[str, Any]] = field(default_factory=list)

    def ask(self, messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        body = {"messages": messages, "temperature": 0, "max_tokens": 256,
                "chat_template_kwargs": {"enable_thinking": False}, **extra}
        return request_json(f"{self.base_url}/v1/chat/completions", payload=body,
                            options=Options(timeout=REPLY_S, headers={"Authorization": f"Bearer {self.key}"}))

    def get(self, path: str) -> Any:
        return request_json(f"{self.base_url}{path}", method="GET",
                            options=Options(timeout=60.0, headers={"Authorization": f"Bearer {self.key}"}))

    def record(self, check: str, passed: bool, **facts: Any) -> None:
        self.rows.append({"check": check, "passed": bool(passed), **facts})
        say(f"{'pass' if passed else 'FAIL'}  {check}  " + "  ".join(f"{k}={v}" for k, v in facts.items()))


def _text(reply: dict[str, Any]) -> str:
    return str(reply["choices"][0]["message"].get("content") or "")


def _speed(reply: dict[str, Any]) -> dict[str, Any]:
    timings = reply.get("timings") or {}
    return {key: timings.get(key) for key in ("prompt_n", "cache_n", "prompt_per_second", "predicted_n", "predicted_per_second")}


def context_and_drafting(proof: Proof) -> None:
    props = proof.get("/props")
    window = int(props["default_generation_settings"]["n_ctx"])
    proof.record("context", window >= 262_144, tokens=window, build=props.get("build_info"))
    found = json.dumps(proof.get("/metrics"))
    drafting = any(f'"drafting": "{name}"' in found for name in ("mtp", "dflash2"))
    proof.record("drafting", drafting, server_said=found[found.find('"drafting"'):][:60] if '"drafting"' in found else "nothing")


def recall(proof: Proof) -> None:
    """A code word hidden at `DEPTHS` of prompts of `LENGTHS` tokens, and the reading and writing speed of each."""
    window = int(proof.get("/props")["default_generation_settings"]["n_ctx"])
    for number, length in enumerate(n for n in LENGTHS if n < window):
        for place, depth in enumerate(DEPTHS):
            lines = filler(length, seed=number * 10 + place)
            code = f"{WORDS[_drawn(length, place)[0]]}-{1000 + number * 7 + place}"
            lines.insert(int(len(lines) * depth), f"The vault code is {code}. Remember it.")
            reply = proof.ask([{"role": "user", "content": "\n".join(lines) + "\n\nWhat is the vault code? Answer with the code only."}])
            proof.record(f"recall {length} at {int(depth * 100)}%", code in _text(reply),
                         asked=code, answered=_text(reply)[:40], **_speed(reply))


def writing(proof: Proof) -> None:
    reply = proof.ask([{"role": "user", "content": "Write a Python function that merges overlapping intervals, with tests."}],
                      max_tokens=600)
    proof.record("writing speed", len(_text(reply)) > 200, **_speed(reply))


def tools(proof: Proof) -> None:
    messages = [{"role": "user", "content": "Use the tool to read the balance of account north-7, then tell me the number."}]
    first = proof.ask(messages, tools=[TOOL])
    message = first["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    arguments = json.loads(calls[0]["function"]["arguments"]) if calls else {}
    called = bool(calls) and calls[0]["function"]["name"] == "read_ledger" and arguments.get("account") == "north-7"
    proof.record("tool call", called, calls=len(calls), arguments=arguments, finish=first["choices"][0].get("finish_reason"))
    if not called:
        return
    messages += [message, {"role": "tool", "tool_call_id": calls[0]["id"], "content": "balance: 48213"}]
    second = proof.ask(messages, tools=[TOOL])
    proof.record("tool answer", "48213" in _text(second) or "48,213" in _text(second), answered=_text(second)[:60], **_speed(second))
    _reused(proof, "reuse after a tool result", first, second)


def _reused(proof: Proof, check: str, earlier: dict[str, Any], later: dict[str, Any]) -> None:
    read = int(earlier["usage"]["prompt_tokens"])
    kept = int(((later.get("usage") or {}).get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    proof.record(check, kept >= REUSED * read, earlier_prompt=read, reused=kept, prompt=later["usage"]["prompt_tokens"])


def reuse(proof: Proof) -> None:
    """A long conversation's next turn, and the one after another conversation came between: a delegate must not reread it."""
    messages = [{"role": "user", "content": "\n".join(filler(24_000, seed=99)) + "\n\nSay only: noted."}]
    first = proof.ask(messages)
    messages += [first["choices"][0]["message"], {"role": "user", "content": "Say only: again."}]
    second = proof.ask(messages)
    _reused(proof, "reuse on the next turn", first, second)
    proof.ask([{"role": "user", "content": "\n".join(filler(8_000, seed=7)) + "\n\nSay only: other."}])
    messages += [second["choices"][0]["message"], {"role": "user", "content": "Say only: once more."}]
    third = proof.ask(messages)
    _reused(proof, "reuse after another conversation", second, third)


def thinking(proof: Proof) -> None:
    question = [{"role": "user", "content": "What is 17 times 23?"}]
    off = proof.ask(question)["choices"][0]["message"]
    proof.record("thinking off", not off.get("reasoning_content") and "391" in str(off.get("content")))
    on = proof.ask(question, chat_template_kwargs={"enable_thinking": True}, max_tokens=2000)["choices"][0]["message"]
    proof.record("thinking on", bool(on.get("reasoning_content")) and "391" in str(on.get("content")),
                 thought_characters=len(on.get("reasoning_content") or ""))


def pictures(proof: Proof) -> None:
    """A picture named by a file or an address is refused: the server reads neither."""
    for named in (str(Path(__file__)), "file://" + str(Path(__file__)), "http://127.0.0.1:1/x.png"):
        content = [{"type": "text", "text": "Describe."}, {"type": "image_url", "image_url": {"url": named}}]
        try:
            reply = proof.ask([{"role": "user", "content": content}], max_tokens=8)
        except ServerError as refused:
            proof.record(f"picture refused ({named.split(':')[0][:8]})", True, said=str(refused)[:80])
        else:
            proof.record(f"picture refused ({named.split(':')[0][:8]})", False, answered=_text(reply)[:40])


CHECKS: tuple[Callable[[Proof], None], ...] = (context_and_drafting, writing, tools, reuse, thinking, pictures, recall)


def run(proof: Proof) -> bool:
    """Every check against ``proof``'s server; a check that cannot get an answer is a failed one."""
    for check in CHECKS:
        try:
            check(proof)
        except (ServerError, OSError, ValueError, KeyError, IndexError) as why:
            proof.record(check.__name__, False, error=str(why)[:200])
    return bool(proof.rows) and all(row["passed"] for row in proof.rows)


OPTIONS = [
    flag("model", help="the first .gguf of a set Strata is pinned to serve, or its hf: reference"),
    flag("--context", type=parse_context, default=262_144, help="the context to serve and prove (default: 262,144)"),
    flag("--draft", default="", choices=["", "mtp", "dflash2"], help="the drafter (default: the best one this device may use)"),
]


def cmd_prove(args: argparse.Namespace) -> int:
    """``poolhouse-serve strata-proof MODEL``."""
    manager = ServerManager()
    started = time.time()
    info = manager.lease(ServerSpec(model=args.model, engine="strata", context=args.context, draft=args.draft or None),
                         timeout=1800.0, reason="live proof of the Strata engine")
    try:
        proof = Proof(info.base_url, serverkeys.for_url(info.base_url))
        say(f"server {info.base_url} loaded in {info.load_s or 0:.0f}s; {info.mtp_note}")
        passed = run(proof)
    finally:
        manager.release(info)
    where = home.state("strata", "proof", f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(started))}.json")
    where.parent.mkdir(parents=True, exist_ok=True)
    write_json(where, {"passed": passed, "model": args.model, "context": args.context, "drafting": info.mtp_note,
                       "load_s": info.load_s, "system": platform.platform(), "machine": platform.machine(), "checks": proof.rows})
    (say if passed else warn)(f"{'passed' if passed else 'FAILED'}; record: {where}")
    return 0 if passed else 1

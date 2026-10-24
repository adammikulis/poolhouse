"""The line a hook shows when messages wait, and the Claude Code hook outputs built from it."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from poolhouse import private_counters
from poolhouse.board import credentials
from poolhouse.board.session import Session
from poolhouse.guard.secrets import env_secrets, redact
from poolhouse.log import warn
from poolhouse.workspace import nudge_fence


def warning(reason: object) -> None:
    """An advisory boundary warning without credentials or terminal controls."""
    text, _ = redact(str(reason), env_secrets(os.environ))
    warn(f'workspace lifecycle: {" ".join(nudge_fence.clean(text).split())[:1000]}')


URGENT = ("question", "task", "handoff", "blocked")
UNCHANGING = frozenset({"status", "blocked", "done"})
STOP_AFTER_S = 120.0
POST_EVERY_S = 20.0
SENDERS_SHOWN = 4
EVENTS = ("post", "stop", "prompt")
NAMES = {"post": "PostToolUse", "prompt": "UserPromptSubmit"}
SAFE = re.compile(r"[^A-Za-z0-9._/-]")


@dataclass(frozen=True, slots=True)
class Waiting:
    """The unread rows for one reader at one moment, and the text of those from senders it may be pushed for."""

    me: str
    rows: list[dict[str, Any]]
    now: float
    messages: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """The reader, the time and each unread row's sequence, sender, kind and time, and the pushable messages."""
        keys = ("id", "seq", "to", "from", "type", "ts")
        return {"me": self.me, "now": self.now, "rows": [{k: r.get(k) for k in keys} for r in self.rows],
                "messages": self.messages}

    @classmethod
    def of(cls, summary: dict[str, Any]) -> Waiting:
        """The `Waiting` a `summary` describes."""
        return cls(str(summary["me"]), list(summary["rows"]), float(summary["now"]),
                   list(summary.get("messages", [])))

    def urgent(self) -> list[dict[str, Any]]:
        """Direct questions, tasks, handoffs and blocked notices addressed to the reader."""
        return [r for r in self.rows if r["to"] == self.me and r.get("type") in URGENT]

    def _kinds(self) -> str:
        counts: dict[str, int] = {}
        for r in self.rows:
            kind = SAFE.sub("", str(r.get("type") or "message"))[:48] or "message"
            counts[kind] = counts.get(kind, 0) + 1
        ordered = sorted(counts.items(), key=lambda kv: (kv[0] not in URGENT, -kv[1], kv[0]))
        return ", ".join(f"{n} {k}{'s' if n > 1 and k not in UNCHANGING else ''}" for k, n in ordered[:4]) + (f", {len(ordered) - 4} other kinds" if len(ordered) > 4 else "")

    def _senders(self) -> str:
        seen: list[str] = []
        for r in self.rows:
            who = SAFE.sub("", str(r["from"]))[:48]
            if who and who not in seen:
                seen.append(who)
        more = f" +{len(seen) - SENDERS_SHOWN} more" if len(seen) > SENDERS_SHOWN else ""
        return ", ".join(seen[:SENDERS_SHOWN]) + more

    def line(self) -> str:
        """The one-line summary, or "" when nothing waits."""
        if not self.rows:
            return ""
        age = age_text(self.now - min(float(r["ts"]) for r in self.rows))
        head = (f"workspace: {len(self.rows)} waiting for you ({self._kinds()}; "
                f"from {self._senders()}; oldest {age})")
        urgent = sorted({r["type"] for r in self.urgent()}, key=URGENT.index)
        if not urgent:
            return f"{head}; run inbox"
        return (f"{head}. A direct {' or '.join(urgent)} is waiting on you: run "
                f"poolhouse-workspace inbox now and answer it")


def age_text(seconds: float) -> str:
    """``seconds`` as 40s, 5m or 3h12m."""
    s = max(int(seconds), 0)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


RETRY_AFTER_S = 120.0
PAGE = 200
STOP_BUDGET = 3


class Attempt(str):
    """Rendered notification and the attempt-only state to keep after local emission."""

    saved: dict[str, dict[str, int]]

    def __new__(cls, text: str, saved: dict[str, dict[str, int]]):
        made = super().__new__(cls, text)
        made.saved = saved
        return made


def _delivery(waiting: Waiting, rows: list[dict[str, Any]]) -> tuple[str, list[str]]:
    pushable = {m["id"]: m for m in waiting.messages}
    blocks, shown, used = [], [], 0
    for row in rows:
        message = pushable.get(row["id"])
        if message is None or len(blocks) == nudge_fence.MESSAGES:
            continue
        text = nudge_fence.block(message)
        if used + len(text) > nudge_fence.DELIVERY_CHARS:
            continue
        blocks.append(text)
        shown.append(row["id"])
        used += len(text)
    rest_rows = [r for r in rows if r["id"] not in shown]
    rest = [SAFE.sub("", str(r["seq"]))[:48] for r in rest_rows]
    parts = [waiting.line(), *([nudge_fence.fenced(blocks)] if blocks else [])]
    if rest:
        listed = ", ".join(rest[:nudge_fence.SEQS_LISTED])
        more = ", ..." if len(rest) > nudge_fence.SEQS_LISTED else ""
        parts.append(f"{len(rest)} more not shown (seq {listed}{more}); run poolhouse-workspace inbox")
    parts.append(nudge_fence.NOTICE)
    # Metadata-only notices also have bounded retries, never a read acknowledgment.
    listed_metadata = [r["id"] for r in rest_rows[:nudge_fence.SEQS_LISTED] if r["id"] not in pushable]
    return "\n".join(parts), shown + listed_metadata


def output(event: str, waiting: Waiting, stdin: str,
           saved: dict[str, dict[str, int]]) -> Attempt:
    """Prepare one bounded local notification attempt; this does not mark anything read."""
    if set(saved) - {"attempts", "budget"}:
        raise ValueError("notification attempt ledger has unsupported fields")
    if set(saved.get("budget", {})) - {"post", "stop", "stops"} or len(saved.get("attempts", {})) > PAGE:
        raise ValueError("notification attempt ledger exceeds its bounded schema")
    if len(waiting.rows) > PAGE:
        raise ValueError("notification candidate page exceeds 200 messages")
    now = max(0, int(waiting.now * 1000))
    budget = dict(saved.get("budget", {}))
    candidates = {str(r["id"]) for r in waiting.rows}
    if any(not item or len(item) > 160 for item in candidates):
        raise ValueError("notification message identifier is malformed")
    attempts = {key: stamp for key, stamp in saved.get("attempts", {}).items() if key in candidates}
    state = {"attempts": attempts, "budget": budget}
    rows = [r for r in waiting.rows if r["id"] not in attempts or now < attempts[r["id"]]
            or now - attempts[r["id"]] >= RETRY_AFTER_S * 1000]
    if event == "post" and 0 <= now - budget.get("post", -int(POST_EVERY_S * 1000)) < POST_EVERY_S * 1000:
        return Attempt("", state)
    if event == "stop":
        active = json.loads(stdin or "{}").get("stop_hook_active", False)
        if active or budget.get("stops", 0) >= STOP_BUDGET or now - budget.get("stop", -120000) < 120000:
            return Attempt("", state)
        rows = [r for r in rows if r["to"] == waiting.me and r.get("type") in URGENT
                and waiting.now - float(r["ts"]) >= STOP_AFTER_S]
    if not rows:
        return Attempt("", state)
    text, shown = _delivery(waiting, rows)
    attempts.update(dict.fromkeys(shown, now))
    if event == "post":
        budget["post"] = now
    if event == "stop":
        budget["stop"] = max(now, budget.get("stop", 0))
        budget["stops"] = budget.get("stops", 0) + 1
        shape = {"systemMessage": text}
    else:
        shape = {"hookSpecificOutput": {"hookEventName": NAMES.get(event, event), "additionalContext": text},
                 "systemMessage": text}
    return Attempt(json.dumps(shape), state)


def deliver(actor: Session, waiting: Waiting, event: str, stdin: str,
            emit: Callable[[str], None] | None) -> str:
    """Bind private state to the authenticated actor; commit only after successful local output."""
    actual = str(actor.call("whoami")["name"])
    if actual != waiting.me:
        raise ValueError("notification actor does not match authenticated reader")
    target = credentials.private(actor.client.state, actor.board, actual) / "notification-attempts.json"
    with private_counters.locked(target, timeout=0.2):
        previous = private_counters.read(target)
        plan = output(event, waiting, stdin, previous)
        if plan and emit is not None:
            if event == "stop":
                private_counters.write(target, {"attempts": previous.get("attempts", {}), "budget": plan.saved["budget"]})
            emit(plan)
            private_counters.write(target, plan.saved)
        return plan

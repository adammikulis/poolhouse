#!/usr/bin/env python3
"""Count the rows of this project's board by writer: the kind of row, what it says, who wrote it, and rows per day.

Reads through the node as the session that runs it, so it sees what that session may see; `--days N` counts only the last N days.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from collections.abc import Iterable

from poolhouse.board import session

DAY_MS = 86_400_000
KINDS = ("message", "lease", "audit", "identity", "landing", "note")
TEST_BODIES = (("test-run", "message status test-run (a file's claim)"), ("test-result", "message status test-result (a file's end)"),
               ("test-job", "message status test-job"), ("done", "message status done (a subagent's stop)"))
VARIABLE = re.compile(r"[0-9a-f]{7,64}|\d+")
HOOKS = frozenset({"runtime", "stopped,", "tree", "landed", "landing", "handed", "stop", "test", "finished", "main", "fork", "Explore",
                   "general-purpose", "branch-worker", "branch-finisher", "workflow-subagent", "claude-code-guide"})
FAMILY = re.compile(r"-[0-9a-f]{6}$")


def family(sender: str) -> str:
    """The sender without its six-character suffix: `claude`, `agent`, `chatgpt`."""
    return FAMILY.sub("", sender)


def _said(text: str) -> str:
    """The first word of an announcement a hook or tool writes, or `free text` for one an agent wrote."""
    word = (VARIABLE.sub("#", text).split() or [""])[0]
    return word if word in HOOKS else "free text"


def _message(fields: dict) -> str:
    kind, body = str(fields.get("type", "")), str(fields.get("body", ""))
    if kind != "status":
        return f"message {kind}: {_said(body)}"
    for prefix, label in TEST_BODIES:
        if body.startswith(prefix):
            return label + (" start" if prefix == "test-job" and "state=running" in body else " end" if prefix == "test-job" else "")
    return f"message status: {_said(str(fields.get('subject', '')))}"


def label(kind: str, fields: dict) -> str:
    """The writer a row belongs to: its kind and what it records, with ids and numbers folded."""
    if kind == "message":
        return _message(fields)
    if kind == "lease":
        resource = str((fields.get("resources") or [""])[0]).split(":")
        return f"lease {resource[1] if len(resource) > 1 else '?'} {fields.get('action', '')}"
    if kind == "audit":
        return f"audit {fields.get('event', '')}"
    if kind == "identity":
        return "identity retired" if fields.get("retired") else "identity registered or updated"
    if kind == "landing":
        return f"landing {fields.get('ev', '')}"
    return kind


def tally(rows: Iterable[tuple[str, str, dict, int]], since_ms: int = 0) -> tuple[Counter, dict[str, Counter], int, int]:
    """Counts by label, the sender families of each label, and the first and last time of the rows counted."""
    counts: Counter = Counter()
    writers: dict[str, Counter] = {}
    first = last = 0
    for kind, sender, fields, at_ms in rows:
        if at_ms < since_ms:
            continue
        name = label(kind, fields)
        counts[name] += 1
        writers.setdefault(name, Counter())[family(sender)] += 1
        first, last = (at_ms if not first else min(first, at_ms)), max(last, at_ms)
    return counts, writers, first, last


def report(counts: Counter, writers: dict[str, Counter], first: int, last: int) -> list[str]:
    """A table, most rows first: rows, rows per day, share, label and its senders."""
    total, days = sum(counts.values()), max((last - first) / DAY_MS, 1 / 24)
    lines = [f"{total} rows over {days:.1f} days, {total / days:.0f} a day"]
    for name, rows in counts.most_common():
        senders = ", ".join(f"{who} {n}" for who, n in writers[name].most_common(3))
        lines.append(f"{rows:7d} {rows / days:8.0f}/day {100 * rows / total:5.1f}%  {name}  ({senders})")
    return lines


def main(argv: list[str] | None = None) -> int:
    """Print the tally of the board of the current directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=float, default=0.0, help="count only the last N days")
    args = parser.parse_args(argv)
    held = session.connect()
    rows = ((e.kind, e.sender, e.fields, e.at_ms) for kind in KINDS for e in held.read_all(kind=kind))
    since = int(time.time() * 1000 - args.days * DAY_MS) if args.days else 0
    print("\n".join(report(*tally(rows, since))))
    return 0


if __name__ == "__main__":
    sys.exit(main())

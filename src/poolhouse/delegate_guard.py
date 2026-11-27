"""``python -m poolhouse.delegate_guard OPTIONS EVENT``: the hook of a delegate that runs without a sandbox, one event on standard input.

Before a tool call it applies the deterministic blocks (`poolhouse.guard.unboxed`) and, in every tier below `yolo`, the classifier under that tier;
the answer is allow or deny at once. Each event is appended to the calls the delegate's runner reads. A failure exits 2, which the plugin that
asks treats as a refusal.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import IO, Any

from poolhouse import coding_policy, harnesspolicy
from poolhouse.errors import Denied
from poolhouse.guard import unboxed
from poolhouse.interventions import Call

__all__ = ["judge", "run"]

KEPT = ("tool_name", "tool_input", "tool_use_id", "loaded")
FAILED = 2


def judge(tier: str, ground: unboxed.Ground, name: str, args: dict[str, Any]) -> str:
    """Why a delegate of ``tier`` may not make this call, empty when it may."""
    if why := unboxed.refusal(name, args, ground):
        return why
    if tier == coding_policy.YOLO:
        return ""
    decision = harnesspolicy.decide(coding_policy.Policy(tier), Call(name, args), roots=(ground.root, ground.scratch))
    return decision.reason if decision.action == "deny" else ""


def run(argv: Sequence[str] | None = None, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
    """Answer one event; 0 when it was answered and recorded."""
    words = list(sys.argv[1:] if argv is None else argv)
    try:
        given, event = dict(zip(words[:-1:2], words[1:-1:2], strict=True)), words[-1]
        os.environ.update(HOME=given["--home"], POOLHOUSE_HOME=given["--state"])   # the rules read the user's own places, not the delegate's home
        payload = json.loads((stdin or sys.stdin).read() or "{}")
        said: dict[str, Any] = {}
        if event == "pre":
            ground = unboxed.Ground(given["--root"], given["--home"], given["--state"], given["--scratch"])
            asked = payload.get("tool_input")
            why = judge(given["--tier"], ground, str(payload.get("tool_name", "")), asked if isinstance(asked, dict) else {})
            said = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny" if why else "allow",
                                           "permissionDecisionReason": f"poolhouse: {why or 'no block applies'}"}}
        row = {"event": event, "payload": {key: payload[key] for key in KEPT if key in payload}, "said": said}
        with Path(given["--calls"]).open("a", encoding="utf-8") as calls:
            calls.write(json.dumps(row) + "\n")
    except (Denied, OSError, RuntimeError, ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError) as why:
        sys.stderr.write(f"poolhouse: the call could not be judged: {type(why).__name__}\n")
        return FAILED
    (stdout or sys.stdout).write(json.dumps(said) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())

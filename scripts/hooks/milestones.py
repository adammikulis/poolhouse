"""The lead's notice when a subagent reaches 100 tool calls and every 50 after; it informs and never limits or stops the subagent.

The count is the wrap-up hook's (`wrap_up.record`). Nothing from poolhouse is imported until a milestone is reached, so an
ordinary tool call stays cheap. The interval is `POOLHOUSE_TOOL_MILESTONE_EVERY` (default 50); the first milestone is 100.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import subagent_records
import wrap_up

FIRST = 100
EVERY = 50
TRIES = 3
SETTING = "POOLHOUSE_TOOL_MILESTONE_EVERY"


def interval() -> int:
    """Tool calls between milestones after the first: the setting, or 50 when it is unset or not a positive whole number."""
    named = os.environ.get(SETTING, "").strip()
    if named.isdigit() and int(named) > 0:
        return int(named)
    if named:
        print(f"workspace PostToolUse: {SETTING}={named!r} is not a positive whole number; using {EVERY}", file=sys.stderr)
    return EVERY


def reached(count: int, every: int) -> int:
    """The latest milestone at or below this count, or 0 before the first."""
    return FIRST + (count - FIRST) // every * every if count >= FIRST else 0


def elapsed(started: object) -> str:
    """How long the subagent has run, as `running 3h06m`, or `elapsed time unknown` when its start was not recorded."""
    if not isinstance(started, (int, float)) or started <= 0:
        return "elapsed time unknown"
    minutes = max(0, int((time.time() - started) // 60))
    return f"running {minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"running {minutes}m"


def notice(event: dict, name: str, count: int) -> str:
    """The one-line announcement: the subagent's board name, its tool calls, how long it has run and what it was asked to do."""
    import workspace_hook
    what = workspace_hook.subagent_meta(event).get("description") or event.get("agent_type") or "subagent"
    started = subagent_records.load(str(event.get("agent_id") or "")).get("started")
    return " ".join(f"milestone: {name} has made {count} tool calls ({elapsed(started)}): {what}".split())[:200]


def tell(event: dict, count: int) -> str:
    """Post the notice as the subagent, so the lead that follows it hears it in its inbox; why it could not, or an empty string."""
    import workspace_hook
    environment = workspace_hook.own_environment(event, "PostToolUse")
    if environment is None:
        return "the board has not named this subagent"
    command = ["poolhouse-workspace", "board", "post", "#general", notice(event, environment["POOLHOUSE_WORKSPACE_AGENT"], count),
               "--type", "status", "--subject", "milestone"]
    done = workspace_hook.attempt(command, "PostToolUse", environment=environment, timeout=10)
    if done is None:
        return "the board did not answer"
    if done.returncode:
        return f"command exited {done.returncode}: {done.stderr or done.stdout or 'no diagnostic returned'}"
    return ""


def failed(agent: str, level: int, count: int, why: str) -> str:
    """A notice that was not delivered: warn at once, try again on the next call, and after three tries tell the subagent."""
    import workspace_hook
    reason = workspace_hook.redacted(why)[:200]
    workspace_hook.warning("PostToolUse", f"milestone notice at {level} tool calls not delivered: {reason}")
    attempt = next((n for n in range(1, TRIES + 1) if wrap_up.claim(agent, level, f"milestone-try{n}")), TRIES)
    if attempt < TRIES:
        Path(f"{wrap_up.stem(agent)}.milestone-{level}").unlink(missing_ok=True)
        return ""
    return (f"The lead could not be told that you reached {count} tool calls ({reason}). Say so in your final report so it can "
            "decide whether to check in.")


def check(event: dict, agent: str, count: int) -> str:
    """Tell the lead if this call is the first to reach a milestone; text for the subagent's context when the lead could not be told."""
    level = reached(count, interval())
    if not level or not wrap_up.claim(agent, level, "milestone"):
        return ""
    try:
        why = tell(event, count)
    except (ImportError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        why = f"{type(error).__name__}: {error}"
    return failed(agent, level, count, why) if why else ""

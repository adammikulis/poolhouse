"""The subscription of a main session to the subagents it starts: it hears each one's done in its inbox."""

from __future__ import annotations

import json

import workspace_hook

SUBSCRIPTIONS = 12


def listed(command: list[str], parent: dict) -> list[dict]:
    """The rows a `--json` workspace command prints as the parent, or an empty list when it printed none."""
    done = workspace_hook.run(["poolhouse-workspace", *command, "--json"], "SubagentStart", environment=parent)
    try:
        rows = json.loads(done.stdout) if done else []
    except ValueError:
        return []
    return [row for row in rows if isinstance(row, dict)]


def forget_retired(parent: dict) -> None:
    """Drop the parent's subscriptions to subagents that ended, when it holds as many as the board allows."""
    held = listed(["subs"], parent)
    if len(held) < SUBSCRIPTIONS:
        return
    live = {row.get("id") for row in listed(["agents"], parent)}
    for row in held:
        if row.get("type") == "agent" and row.get("target") not in live:
            workspace_hook.run(["poolhouse-workspace", "unsubscribe", "agent", str(row["target"])], "SubagentStart",
                               environment=parent)


def follow(event: dict, parent: dict, child: str) -> None:
    """Subscribe the main session that started the subagent to it in inbox mode; a nested subagent's parent has none."""
    if workspace_hook.spawner_id(event):
        return
    forget_retired(parent)
    workspace_hook.run(["poolhouse-workspace", "subscribe", "agent", child, "--mode", "inbox", "--force"],
                       "SubagentStart", environment=parent)

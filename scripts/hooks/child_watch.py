"""The subscription of a main session to the subagents it starts: it hears each one's done in its inbox."""

from __future__ import annotations

import workspace_hook


def follow(event: dict, parent: dict, child: str) -> None:
    """Subscribe the main session that started the subagent to it in inbox mode; a nested subagent's parent has none."""
    if workspace_hook.spawner_id(event):
        return
    workspace_hook.run(["poolhouse-workspace", "subscribe", "agent", child, "--mode", "inbox", "--force"],
                       "SubagentStart", environment=parent)

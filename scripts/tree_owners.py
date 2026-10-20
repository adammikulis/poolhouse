"""The worktrees a live agent holds: the claims on the board and the trees of registered subagents."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "hooks"))

import subagent_records
import workspace_hook

STAGE = "worktrees sweep"


def claimed() -> set[str]:
    """The keys of the live worktree and branch claims; empty, with a warning, when the board does not answer."""
    done = workspace_hook.run(["poolhouse-workspace", "claims", "--json"], STAGE)
    try:
        rows = json.loads(done.stdout) if done else []
    except ValueError as error:
        workspace_hook.warning(STAGE, f"claims were not JSON: {error}")
        return set()
    return {row["key"] for row in rows if isinstance(row, dict) and row.get("kind") in ("branch", "worktree")
            and isinstance(row.get("key"), str)}


def registered() -> set[str]:
    """The directory names (``agent-ID``) the harness made for the subagents whose identity is not retired."""
    found = set()
    for record in subagent_records.folder().glob("*.json"):
        try:
            value = json.loads(record.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and value.get("name") and isinstance(value.get("agent_id"), str):
            found.add(f"agent-{value['agent_id']}")
    return found


def owned() -> set[str]:
    """Every path, branch and directory name a live agent holds."""
    return claimed() | registered()

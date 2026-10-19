"""What the hooks keep about each subagent of the user: the board name and board the start hook registered it under."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import workspace_hook


def folder() -> Path:
    """The private directory holding one record per subagent."""
    path = workspace_hook.state_dir() / "subagents"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def path_of(agent_id: str) -> Path:
    """The record file of the subagent with this harness agent id."""
    return folder() / (hashlib.sha256(agent_id.encode()).hexdigest()[:24] + ".json")


def load(agent_id: str) -> dict:
    """The record of this subagent, or an empty dict when there is none or it cannot be read."""
    try:
        value = json.loads(path_of(agent_id).read_text())
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save(agent_id: str, **fields: object) -> dict:
    """Merge ``fields`` into the record of this subagent; the record written."""
    record = {**load(agent_id), "agent_id": agent_id, **fields}
    target = path_of(agent_id)
    temporary = target.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(record))
    temporary.chmod(0o600)
    temporary.replace(target)
    return record


def native_key(agent_id: str) -> str:
    """The session id the board knows this subagent by: its agent id, or that id and a number once an earlier identity ended."""
    generation = int(load(agent_id).get("generation") or 1)
    return agent_id if generation <= 1 else f"{agent_id}.{generation}"


def next_identity(agent_id: str) -> str:
    """Move the subagent on to a new board identity (its earlier one ended); the session id that identity is registered under."""
    save(agent_id, generation=int(load(agent_id).get("generation") or 1) + 1, name="")
    return native_key(agent_id)

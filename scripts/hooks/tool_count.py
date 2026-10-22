"""A subagent's tool-call count and the exclusive markers kept beside it; imports nothing from poolhouse so each call stays cheap."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import hook_state


def stem(agent_id: str) -> Path:
    """The path prefix of this subagent's count file and markers."""
    folder = hook_state.state_dir() / "tool-count"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder / hashlib.sha256(agent_id.encode()).hexdigest()[:24]


def mark(path: str) -> bool:
    """Create the marker file ``path`` exclusively; False when it was already there (another call got it first)."""
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return False
    return True


def record(agent_id: str, call_id: str = "") -> int:
    """Count one more tool call of this subagent (one appended byte) and return the count.

    A call named by ``call_id`` (the event's tool_use_id) is counted once however often its event is delivered; a repeat
    returns 0 and leaves the count alone.
    """
    prefix = stem(agent_id)
    if call_id and not mark(f"{prefix}.seen-{hashlib.sha256(call_id.encode()).hexdigest()[:16]}"):
        return 0
    descriptor = os.open(f"{prefix}.count", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(descriptor, b".")
        return os.fstat(descriptor).st_size
    finally:
        os.close(descriptor)


def claim(agent_id: str, kind: str, level: int) -> bool:
    """Whether this call is the first to claim the ``kind`` marker for ``level`` (an exclusive marker file decides)."""
    return mark(f"{stem(agent_id)}.{kind}-{level}")


def release(agent_id: str, kind: str, level: int) -> None:
    """Give a claimed marker back, so that the next call claims it again."""
    Path(f"{stem(agent_id)}.{kind}-{level}").unlink(missing_ok=True)


def clear(agent_id: str) -> None:
    """Forget this subagent's count and markers."""
    prefix = stem(agent_id)
    for path in prefix.parent.glob(f"{prefix.name}.*"):
        path.unlink(missing_ok=True)

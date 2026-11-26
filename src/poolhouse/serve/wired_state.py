"""What the wiring limit is now and whether a boot-time setting of it exists, read without root. Poolhouse
reads the limit and sizes every fit and lease by it; it never changes it, because changing it needs
administrator rights and nothing in Poolhouse asks for a password or runs sudo."""

from __future__ import annotations

import plistlib
from dataclasses import dataclass
from pathlib import Path

from poolhouse.serve.wired import Hooks

__all__ = ["DAEMON", "LABEL", "State", "state"]

LABEL = "stack.ml.wired-limit"
DAEMON = Path(f"/Library/LaunchDaemons/{LABEL}.plist")


@dataclass(frozen=True, slots=True)
class State:
    """Whether the limit is kept across restarts by a boot-time setting, and whether the live value agrees."""

    kept: bool
    kept_mb: int
    live_mb: int
    drift: bool


def state(hooks: Hooks | None = None) -> State:
    """Whether a boot-time setting of the limit is installed and what it sets."""
    hooks = hooks or Hooks()
    live, kept_mb = hooks.live_mb(), 0
    try:
        args = plistlib.loads((hooks.daemon or DAEMON).read_bytes()).get("ProgramArguments", [])
        kept_mb = int(str(args[-1]).split("=")[-1])
    except (OSError, ValueError, IndexError, plistlib.InvalidFileException):
        kept_mb = 0
    return State(bool(kept_mb), kept_mb, live, bool(kept_mb) and live != kept_mb)

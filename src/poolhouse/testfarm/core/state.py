"""Where the runner keeps what it remembers: the platform's state location for an account, in a directory of its own."""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = ["state_dir"]


def state_dir(account_home: Path) -> Path:
    """The runner's state directory for the account whose home directory is ``account_home``; the directory is not made."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or account_home / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = account_home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or account_home / ".local" / "state")
    return base / "poolhouse-testfarm"

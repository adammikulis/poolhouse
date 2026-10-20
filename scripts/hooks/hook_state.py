"""Where hooks keep their small per-session memory."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def state_dir() -> Path:
    """Where hooks keep their small per-session memory (attention shown); private to the user."""
    named = os.environ.get('POOLHOUSE_HOOK_STATE')
    # Windows has no uid; its temporary directory is already the user's own.
    owner = os.getuid() if hasattr(os, 'getuid') else 'user'
    path = Path(named) if named else Path(tempfile.gettempdir()) / f'poolhouse-hooks-{owner}'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path

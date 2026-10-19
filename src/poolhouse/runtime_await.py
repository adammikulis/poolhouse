"""Wait for a commit on the development branch to reach the remote before a runtime build starts."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from poolhouse import runtime, runtime_deploy, runtime_source
from poolhouse.lock import held_by, only_one

AWAIT_S = 900.0
POLL_S = 2.0
LOCK = "publish.lock"


def waiting() -> str:
    """Who is waiting for a push, as the lock records it, or an empty string."""
    return held_by(runtime.directory() / LOCK)


def published(checkout: Path, commit: str) -> bool:
    """Whether `commit` is on the development branch the remote holds; a commit the local branch lacks raises `DeployError`."""
    try:
        runtime_source.admit(checkout, commit)
    except runtime_source.Unpublished:
        return False
    return True


def until_published(checkout: Path, commit: str, seconds: float, *, sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> bool:
    """Wait up to `seconds`, at most `AWAIT_S`, for `commit` to reach the remote; false when it did not, `Busy` when another process waits."""
    with only_one(runtime_deploy.prepare_root() / LOCK, wait=False, note="runtime build waiting for a push"):
        deadline = clock() + min(AWAIT_S, seconds)
        while not published(checkout, commit):
            if clock() >= deadline:
                return False
            sleep(POLL_S)
        return True

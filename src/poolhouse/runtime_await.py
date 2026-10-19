"""Wait for the development branch's tip to reach the remote before a runtime build starts."""

from __future__ import annotations

import time
from collections.abc import Callable

from poolhouse import runtime, runtime_deploy, runtime_source
from poolhouse.lock import held_by, only_one

AWAIT_S = 900.0
POLL_S = 2.0
LOCK = "publish.lock"


def waiting() -> str:
    """Who is waiting for a push, as the lock records it, or an empty string."""
    return held_by(runtime.directory() / LOCK)


def following[T](plan_for: Callable[[], T], seconds: float, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> T:
    """The result of `plan_for` once it stops raising `Unpublished`, asked again every `POLL_S` for at most `seconds` (never more than `AWAIT_S`).

    It holds the publish lock for the whole wait, so the tip is followed as the branch moves; `Busy` when another process
    waits, `DeployError` when the wait ran out.
    """
    limit = min(AWAIT_S, seconds)
    with only_one(runtime_deploy.prepare_root() / LOCK, wait=False, note="runtime build waiting for a push"):
        deadline = clock() + limit
        while True:
            try:
                return plan_for()
            except runtime_source.Unpublished as unpublished:
                if clock() >= deadline:
                    raise runtime_deploy.DeployError(f"{unpublished.commit[:7]} did not reach origin within {max(0.0, limit):.0f}s; "
                                                     "no runtime was built") from unpublished
                sleep(POLL_S)

"""The worker's hold on its model on this device: a `model_slot` lease of the node, taken once the model is served."""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from poolhouse.board.client import NodeError
from poolhouse.board.session import Session
from poolhouse.workspace import localagent as la, localmodel

__all__ = ["TTL_S", "acquire", "held", "kept", "model_of", "release", "renew"]

TTL_S = 600


def model_of(agent: la.Agent) -> str:
    """The model id the worker's slot and its task assignments name."""
    return localmodel.model_identity(agent.model_name or agent.model)


def acquire(s: Session, agent: la.Agent) -> str:
    """The id of the model slot lease ``s`` holds for ``agent`` while this process runs; RuntimeError when another
    session holds the slot of that model on this device."""
    got = s.call("lease_acquire", resources=[{"type": "model_slot", "model": model_of(agent), "context": agent.ctx, "parallel": 1}],
                 ttl_s=TTL_S, pid=os.getpid(), wait=False)
    if got.get("state") == "busy":
        raise RuntimeError(f"another session holds {model_of(agent)} on this device: {', '.join(str(b) for b in got.get('blockers', []))}")
    return str(got["id"])


def renew(s: Session, lease: str) -> None:
    """Extend the slot lease by its full time."""
    s.call("lease_renew", id=lease, ttl_s=TTL_S)


def release(s: Session, lease: str) -> None:
    """Give the slot lease up."""
    s.call("lease_release", id=lease)


@contextmanager
def kept(s: Session, lease: str, *, every_s: float = TTL_S / 5) -> Iterator[None]:
    """Renew the slot lease on a thread while the block runs, so a long task does not outlive its slot."""
    done = threading.Event()

    def renewing() -> None:
        while not done.wait(every_s):
            with contextlib.suppress(NodeError, OSError):
                renew(s, lease)

    watcher = threading.Thread(target=renewing, daemon=True)
    watcher.start()
    try:
        yield
    finally:
        done.set()
        watcher.join(timeout=2)


def held(s: Session, worker: str) -> dict[str, Any] | None:
    """The model slot lease ``worker`` holds on this board now, or None."""
    for lease in s.call("lease_list")["leases"]:
        if lease["state"] == "held" and lease["holder"] == f"{s.board}/{worker}" \
                and any(r["type"] == "model_slot" for r in lease["resources"]):
            return dict(lease)
    return None

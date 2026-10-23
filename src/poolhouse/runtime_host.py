"""Put the running host (the daemon Poolhouse starts) onto the runtime `poolhouse runtime ensure` selected.

The host only ever stops through its own launcher control, the job-preserving replacement `poolhouse --restart` asks for: it
drains requests, checkpoints jobs and closes its stores before it exits. Nothing here signals a process, so a store writer is
never killed; a host that cannot be replaced that way is reported and left running.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from poolhouse import jobs, runtime, runtime_identity, spawned
from poolhouse.fleet import launch
from poolhouse.runtime_deploy import Outcome

RESTART_WAIT_S = 120.0
POLL_S = 0.5
HEALTH_TRIES = 3
"""A loaded machine can miss one answer; nothing is declared not running on a single miss."""
OLD_HOST = ("this host predates graceful replacement and cannot be restarted from here: "
            "quit and reopen Poolhouse once; every later deploy restarts it by itself")


def _detach(module: str, argv: Sequence[str], *, log: Path) -> spawned.Child:
    return spawned.child_of(jobs.detach(module, argv, log=log, disclaimed=True))


def _health(port: int) -> dict | None:
    for attempt in range(HEALTH_TRIES):
        if attempt:
            time.sleep(POLL_S)
        if (said := launch.already_running(port)) is not None:
            return said
    return None


class _Target(NamedTuple):
    commit: str
    prefix_digest: str
    previous: str = ""


def _ready(said: dict | None, target: _Target) -> bool:
    if said is None:
        return False
    generation = said.get("launcher_control")
    return (isinstance(generation, str) and re.fullmatch("[a-f0-9]{32}", generation) is not None
            and generation != target.previous and said.get("commit") == target.commit
            and said.get("runtime_prefix_digest") == target.prefix_digest and _as_poolhouse(said))


def _as_poolhouse(said: dict) -> bool:
    """Whether the host runs as the Poolhouse executable, where a Mac's installed software must (a host that predates it does not say)."""
    process = said.get("process")
    return not runtime_identity.applies() or (isinstance(process, dict) and process.get("bundle_id") == runtime_identity.BUNDLE_ID)


def _waited(port: int, target: _Target, wait_s: float, health: Callable[[int], dict | None],
            child: spawned.Child | None = None) -> bool:
    """Whether the host reports ``target`` within ``wait_s``; `spawned.StartFailed` when the
    restart process (``child``) ends with an error first."""
    deadline = time.monotonic() + wait_s
    while True:
        said = health(port)
        if _ready(said, target):
            return True
        code = child.poll() if child is not None else None
        if code:
            raise spawned.StartFailed(spawned.explain(
                code, what="the restart of the host", log=runtime.directory() / "restart.log"), "exited")
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_S)


class Seams(NamedTuple):
    """What restart reaches outside itself for: the detached process it runs and the health probe it asks."""

    spawn: Callable[..., spawned.Child | None] = _detach
    health: Callable[[int], dict | None] = _health
    held: Callable[[int], bool] = launch.port_held


def restart(port: int, root: Path, *, wait_s: float = RESTART_WAIT_S, force: bool = False,
            seams: Seams | None = None) -> Outcome:
    """Replace the host on `port` with one running the selected runtime, keeping its port and root.

    The replacement is a detached `poolhouse --restart` run by the selected runtime: it asks the old host to stop at the
    job-preserving boundary, waits for its port to free and starts the new host in the same place. With `wait_s` above 0
    this returns once a different launcher generation reports the exact selected commit and runtime prefix.
    """
    chosen = runtime.available()
    if chosen is None:
        return Outcome("failed", detail="no verified runtime is selected; run `poolhouse runtime ensure` first")
    spawn, health, held = seams or Seams()
    running = health(port)
    if running is None and held(port):
        return Outcome("failed", chosen.commit, f"something holds port {port} and did not answer /health; retry shortly")
    if running is None:
        return Outcome("not-running", chosen.commit, f"nothing answers on port {port}; the next start runs the selected runtime")
    target = _Target(chosen.commit, hashlib.sha256(str(chosen.prefix.resolve()).encode()).hexdigest())
    if not force and _ready(running, target):
        return Outcome("current", chosen.commit, f"the host on port {port} already runs it")
    previous = running.get("launcher_control")
    if not isinstance(previous, str) or not re.fullmatch("[a-f0-9]{32}", previous):
        return Outcome("failed", chosen.commit, OLD_HOST)
    child = spawn("poolhouse.fleet.launch", ["--restart", "--no-browser", "--port", str(port), "--root", str(root)],
                  log=runtime.directory() / "restart.log")
    if wait_s <= 0:
        return Outcome("restarting", chosen.commit, f"replacing the host on port {port}; log: {runtime.directory() / 'restart.log'}")
    try:
        confirmed = _waited(port, target._replace(previous=previous), wait_s, health, child)
    except spawned.StartFailed as exc:
        return Outcome("failed", chosen.commit, str(exc))
    if confirmed:
        return Outcome("restarted", chosen.commit, f"the host on port {port} runs it")
    return Outcome("failed", chosen.commit, f"the host on port {port} still runs its previous generation or has not confirmed the selected runtime after {wait_s:.0f}s; see restart.log")

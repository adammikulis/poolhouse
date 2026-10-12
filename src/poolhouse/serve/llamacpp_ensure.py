"""``llama-cpp ensure``: build upstream master when the active build is behind and nothing is computing."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from poolhouse import net
from poolhouse.home import state
from poolhouse.serve import lease_cli, llamacpp_state, llamacpp_status, llamacpp_update
from poolhouse.serve.broker import BrokerError
from poolhouse.serve.llamacpp_upstream import Newest, Upstream, tag_number
from poolhouse.serve.manager import measurement_said
from poolhouse.serve.process import measuring, pid_exists

__all__ = ["Env", "Result", "Standing", "build_age_hours", "busy_reason", "default_env", "ensure",
           "running_queue", "standing"]

SHORT = 9
HOUR = 3600.0


@dataclass(frozen=True, slots=True)
class Result:
    """What `ensure` did: ``state`` (updated, current, deferred, skipped, failed, pinned, offline,
    approval), the one line it says, its exit code and the build it ended on."""

    state: str
    line: str
    code: int = 0
    build: str = ""


def running_queue() -> dict[str, Any] | None:
    """The bench queue that is running on this machine, or None."""
    try:
        held = json.loads((state("bench") / "queue.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(held, dict) and not held.get("ended") and pid_exists(held.get("pid")):
        return held
    return None


def build_age_hours() -> float | None:
    """How many hours ago the active build was made, or None when it is not a managed build."""
    here = llamacpp_state.active()
    try:
        return (time.time() - datetime.fromisoformat(str(here.info["built_at"])).timestamp()) / HOUR
    except (AttributeError, KeyError, ValueError):
        return None


@dataclass(slots=True)
class Env:
    """What `ensure` runs with: upstream, the net pipeline, the status check, the update, the
    broker's lease table, the measuring lock, the running queue, the clock and the active build's age."""

    upstream: Upstream = field(default_factory=Upstream)
    pipeline: net.Pipeline = field(default_factory=net.default)
    jobs: int = 0
    say: Callable[[str], None] = lambda _text: None
    status: Callable[..., dict] = llamacpp_status.gather
    update: Callable[..., llamacpp_update.Outcome] = llamacpp_update.update
    leases: Callable[[], dict] = lease_cli.snapshot
    measuring: Callable[[], dict | None] = measuring
    queue: Callable[[], dict | None] = running_queue
    now: Callable[[], float] = time.time
    age_hours: Callable[[], float | None] = build_age_hours


def default_env(upstream: Upstream | None, pipeline: net.Pipeline | None, jobs: int,
                say: Callable[[str], None] = lambda _text: None) -> Env:
    """An `Env` over the real machine, with ``upstream`` and ``pipeline`` when given."""
    env = Env(jobs=jobs, say=say)
    env.upstream = upstream or env.upstream
    env.pipeline = pipeline or env.pipeline
    return env


def busy_reason(env: Env) -> str:
    """Why a compile or a binary switch would disturb the machine now, or an empty string."""
    try:
        table = env.leases()
    except (BrokerError, OSError):
        table = {}
    for server in table.get("servers") or ():
        if server.get("holders"):
            return f"a model is leased for work ({Path(str(server.get('model'))).name} on :{server.get('port')})"
    if table.get("queue"):
        return "a lease is waiting for the card"
    if held := env.measuring():
        return f"a measurement holds the card: {measurement_said(held)}"
    if running := env.queue():
        return f"a bench queue is running ({running.get('file', '?')}, pid {running.get('pid')})"
    return ""


def _remember(env: Env, got: dict) -> None:
    up = got.get("upstream") or {}
    if up.get("tag") or up.get("master"):
        llamacpp_state.remember_seen(str(up.get("tag") or ""), str(up.get("master") or ""), at=env.now())


def _unreachable(got: dict) -> Result:
    note = got["upstream_note"]
    if "not approved" in got["newer"]:
        return Result("approval", f"approval: the source host needs approval ({note})", 3)
    return Result("offline", "offline: upstream llama.cpp could not be checked"
                  + (f" ({note})" if note else "") + "; nothing changed", 2)


def ensure(env: Env | None = None, *, max_behind_hours: float = 0.0) -> Result:
    """Check upstream once and, when master is ahead of the active build, the track is not pinned
    and the machine is idle, build and activate it. Never prunes. `BuildFailed` for a missing tool;
    `net.NeedsApproval` when the source host is not approved."""
    env = env or Env()
    got = env.status(env.upstream, env.pipeline)
    up = got["upstream"] or {}
    if not (up.get("master") or up.get("tag")):
        return _unreachable(got)
    _remember(env, got)
    master = str(up.get("master") or "")
    mine = f"build {got['build']}" if got["build"] is not None else "no build"
    behind = got["newer"] == "yes" or (got["build"] is None and not got["commit"])
    if not behind:
        return Result("current", f"current: {mine} is the newest upstream", build=str(got["build"] or ""))
    if got["pinned"]:
        return Result("pinned", f"pinned: the track is pinned to {got['pinned']}, so newer upstream builds are "
                      "not used; `poolhouse-serve llama-cpp pin --off` resumes tracking", 2)
    age = env.age_hours()
    if max_behind_hours and age is not None and age < max_behind_hours:
        return Result("current", f"current: {mine} was made {age:.0f}h ago, within the {max_behind_hours:g}h allowed")
    failed = llamacpp_state.load()["failed"]
    if master and failed.get("commit") == master:
        return Result("skipped", f"skipped: master {master[:SHORT]} failed its smoke test "
                      f"({failed.get('detail') or 'no detail'}); it is not built again until upstream moves")
    if why := busy_reason(env):
        return Result("deferred", f"deferred: {why}")
    outcome = env.update(master or str(up.get("tag")),
                         llamacpp_update.Env(env.upstream, env.pipeline, jobs=env.jobs, say=env.say))
    if outcome.status == "failed":
        llamacpp_state.record_failure(master, outcome.detail, at=env.now())
        return Result("failed", f"failed: {outcome.build} did not pass its smoke test ({outcome.detail}); "
                      "the active build is unchanged", 1, outcome.build)
    if outcome.status == "current":
        return Result("current", f"current: {outcome.detail}", build=outcome.build)
    return Result("updated", f"updated: {mine} -> {outcome.build}", build=outcome.build)


@dataclass(frozen=True, slots=True)
class Standing:
    """Where the active build stands against upstream master: its number, the builds it is behind
    by, its age in hours and whether upstream could be asked."""

    build: int | None
    behind: int | None
    age_hours: float | None
    newer: str
    note: str = ""

    @property
    def stale(self) -> bool:
        return self.newer == "yes"


def standing(env: Env | None = None, *, reuse_s: float = 0.0) -> Standing:
    """The active build against upstream master. An upstream answer younger than ``reuse_s``
    seconds is reused instead of asking again."""
    env = env or Env()
    seen, cached = llamacpp_state.load()["seen"], None
    if reuse_s and seen and env.now() - float(seen.get("at") or 0) < reuse_s:
        cached = Newest(str(seen.get("tag") or ""), master=str(seen.get("master") or ""))
    got = env.status(env.upstream, env.pipeline, newest=cached) if cached else env.status(env.upstream, env.pipeline)
    if not cached:
        _remember(env, got)
    now = tag_number(str((got["upstream"] or {}).get("tag") or ""))
    gap = now - got["build"] if now is not None and got["build"] is not None and now > got["build"] else None
    return Standing(got["build"], gap, env.age_hours(), got["newer"], got["upstream_note"])

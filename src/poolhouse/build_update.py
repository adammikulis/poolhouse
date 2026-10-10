"""Keep this device on the newest build its pool runs, automatically and without asking.

A node advertises its build and learns its members'; one that hears of a newer build is catching up and takes no work.
This brings it current at a safe boundary: never while a lease is held or queued or a runtime build runs. A source
checkout fast-forwards its development branch from its own origin and runs `runtime ensure` (build, health check, node
swap, rollback); a release install takes the signed update path. A failed update selects the previous runtime again and
backs off (5 minutes doubling to 6 hours). Every outcome is audited; nothing prompts. `poolhouse runtime pin` turns it off.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from poolhouse import devbranch, node_pool, person, runtime, runtime_store
from poolhouse.activity import writer
from poolhouse.fleet import updates
from poolhouse.lock import held_by

KIND = "runtime.update"
PIN_ENV = "POOLHOUSE_BUILD_PINNED"
OFF_ENV = "POOLHOUSE_BUILD_UPDATE"
BACKOFF_S = 300.0
BACKOFF_MAX_S = 6 * 3600.0
EVERY_S = 120.0


@dataclass(frozen=True, slots=True)
class Result:
    """What one look at the pool's build did: current, pinned, off, no-node, waiting, updated or failed."""

    action: str
    detail: str = ""
    target: int = 0


def pinned() -> bool:
    """Whether a person pinned this device's build (the environment or ``poolhouse runtime pin``)."""
    return os.environ.get(PIN_ENV, "").lower() in {"1", "yes", "on"} or runtime_store.read_state().get("pinned") is True


def pin(on: bool) -> None:
    """Pin this device's build, or let it follow the pool again."""
    runtime_store.write_state({"pinned": True if on else None})


def count(commit: str, checkout: Path | None) -> int:
    """How many commits sit behind ``commit``: the order builds are compared in. 0 when it cannot be told."""
    if not runtime.COMMIT.fullmatch(commit) or checkout is None:
        return 0
    known = runtime_store.read_state().get("seqs")
    known = known if isinstance(known, dict) else {}
    if isinstance(known.get(commit), int):
        return known[commit]
    done = subprocess.run(["git", "-C", str(checkout), "rev-list", "--count", "--end-of-options", commit], capture_output=True, text=True, timeout=60, check=False)
    if done.returncode or not done.stdout.strip().isdigit():
        return 0
    runtime_store.write_state({"seqs": {**known, commit: int(done.stdout)}})
    return int(done.stdout)


def checkout_of() -> Path | None:
    """The source checkout the installed runtime was built from, or None for a release install."""
    named = runtime_store.read_state().get("checkout")
    return Path(str(named)) if named and (Path(str(named)) / ".git").exists() else None


def node_args() -> list[str]:
    """The flags that tell a starting node which build it is: the selected runtime's commit and order, and whether it is pinned."""
    commit = str(runtime_store.selection().get("commit", ""))
    commit = commit if runtime.COMMIT.fullmatch(commit) else ""
    args = ["--build-id", commit, "--build-seq", str(count(commit, checkout_of()))] if commit else []
    return [*args, *(["--pinned"] if pinned() else [])]


def newest(pool: dict) -> dict | None:
    """The active member running the newest build, when it is newer than this device's; else None."""
    mine = int((pool.get("build") or {}).get("build_seq") or 0)
    rows = [m for m in pool.get("members", []) if m.get("status") == "active" and not m.get("self") and int(m.get("build_seq") or 0) > mine]
    return max(rows, key=lambda m: int(m["build_seq"])) if rows else None


def in_the_way(pool: dict) -> str:
    """Why this device cannot be interrupted now, or ''."""
    leases = pool.get("leases") or {}
    if leases.get("held") or leases.get("queued"):
        return f"a lease is held or queued ({leases.get('held', 0)} held, {leases.get('queued', 0)} queued)"
    lock = runtime.directory() / "deploy.lock"
    return f"a runtime build is running ({holder})" if lock.exists() and (holder := held_by(lock)) else ""


def fast_forward(checkout: Path) -> tuple[int, str]:
    """Fetch the development branch from the checkout's own origin and fast-forward it; never anything else."""
    run = updates.git_in(checkout)
    branch = devbranch.development_branch(checkout)
    rc, current = run(["branch", "--show-current"])
    if rc or current != branch:
        return 1, f"{checkout} is on {current or 'a detached HEAD'}, not the development branch {branch}"
    rc, url = run(["config", "--get", "remote.origin.url"])
    if rc or not url:
        return 1, f"{checkout} has no origin to follow"
    if not updates.BRANCH.fullmatch(branch) or ".." in branch or branch.endswith((".lock", "/")):
        return 1, f"{branch!r} is not a branch name"
    if Path(url).is_dir():  # an origin on this machine (a bare repository): no network, so no network guard
        done = subprocess.run(["git", "-C", str(checkout), "fetch", "--", url, branch], capture_output=True, text=True, timeout=updates.GIT_TIMEOUT, check=False)
        rc, text = done.returncode, f"{done.stdout}{done.stderr}".strip()
    else:
        try:
            updates.check_remote(url, branch)
        except updates.UpdateError as exc:
            return 1, str(exc)
        rc, text = run(["fetch", "--", url, branch])
    if rc:
        return rc, f"could not fetch {branch}: {text}"
    return run(["merge", "--ff-only", "FETCH_HEAD"])


def rebuild(checkout: Path) -> tuple[int, str]:
    """Build, check, select and swap in the checkout's HEAD: `runtime ensure`, which rolls back on its own failures.

    A process an agent started may not run a deploy in the foreground; it asks for the background build the hooks use.
    """
    argv = [sys.executable, "-m", "poolhouse.runtime_cli", "ensure", "--no-update", "--checkout", str(checkout), *(["--background"] if person.marked() else [])]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=updates.PIP_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)[-2000:]
    return done.returncode, f"{done.stdout}{done.stderr}".strip()[-2000:]


def reselect(commit: str) -> tuple[int, str]:
    """Select the runtime of ``commit`` again after a failed update."""
    done = subprocess.run([sys.executable, "-m", "poolhouse.runtime_cli", "rollback", "--to", commit], capture_output=True, text=True, timeout=600, check=False)
    return done.returncode, f"{done.stdout}{done.stderr}".strip()[-500:]


@dataclass(frozen=True, slots=True)
class Steps:
    """The seams a test replaces: git, the build, the rollback, the release path and the clock."""

    fetch: Callable[[Path], tuple[int, str]] = fast_forward
    ensure: Callable[[Path], tuple[int, str]] = rebuild
    rollback: Callable[[str], tuple[int, str]] = reselect
    release: Callable[[], dict] = updates.apply_if_newer
    selected: Callable[[], str] = updates.selected_commit
    now: Callable[[], float] = time.time


def _record(result: Result, fails: int, next_at: float, now: float) -> Result:
    runtime_store.write_state({"update": {"last": result.action, "detail": result.detail[:300], "target": result.target, "at": now,
                                          "fails": fails, "next_at": next_at}})
    return result


def _audit(result: Result, agent: str = "") -> Result:
    writer.record(KIND, actor=f"agent:{agent}" if agent else None, subject=str(runtime_store.selection().get("commit", "")), outcome=result.action,
                  meta={"command": "update", "agent": agent, "person": not person.marked(), "detail": result.detail[:150]})
    return result


def update_once(state: Path | None = None, *, steps: Steps | None = None, agent: str = "", pool: dict | None = None) -> Result:
    """Look at the pool's builds and bring this device current when it is behind and nothing is in the way."""
    steps = steps or Steps()
    if os.environ.get(OFF_ENV) == "off":
        return Result("off", f"{OFF_ENV}=off")
    if pinned():
        return Result("pinned", "this device's build is pinned: it is left as it is")
    pool = pool if pool is not None else node_pool.status(state)
    if pool is None:
        return Result("no-node", "no node answers, so no member's build is known")
    ahead = newest(pool)
    if ahead is None:
        runtime_store.write_state({"update": None})
        return Result("current")
    target, now = int(ahead["build_seq"]), steps.now()
    held = runtime_store.read_state().get("update")
    held = held if isinstance(held, dict) and held.get("target") == target else {}
    if now < float(held.get("next_at") or 0):
        return Result("waiting", f"backing off until the next try after {held.get('detail', 'a failed update')}", target)
    if why := in_the_way(pool):
        return Result("waiting", f"{why}; the update waits", target)
    return _audit(_catch_up(steps, target, int(held.get("fails") or 0), now), agent)


def _catch_up(steps: Steps, target: int, fails: int, now: float) -> Result:
    previous, checkout = steps.selected(), checkout_of()
    if checkout is None:
        done = steps.release()
        code, text = (0, str(done.get("version", ""))) if done.get("ok") else (1, str(done.get("error", "release update failed")))
    else:
        code, text = steps.fetch(checkout)
        if code == 0:
            code, text = steps.ensure(checkout)
    if code == 0 and checkout is not None and person.marked():
        return _record(Result("started", "the build runs in the background; it is tried again after a wait if the device is still behind", target),
                       fails + 1, now + min(BACKOFF_S * 2 ** fails, BACKOFF_MAX_S), now)
    if code == 0 and (checkout is None or steps.selected() != previous):
        return _record(Result("updated", text[-200:], target), 0, 0.0, now)
    if previous and steps.selected() != previous:
        steps.rollback(previous)
        text = f"{text}; rolled back to {previous[:7]}"
    wait = min(BACKOFF_S * 2 ** fails, BACKOFF_MAX_S)
    return _record(Result("failed", text[-300:] or "the build did not become the selected runtime", target), fails + 1, now + wait, now)


def last_line() -> str:
    """The last update this device tried, for `runtime status`, or ''."""
    last = runtime_store.read_state().get("update")
    return f"update       {last.get('last')}: {last.get('detail', '')}" if isinstance(last, dict) and last.get("last") else ""


def watch(state: Path, stopping: Callable[[], bool], *, every: float = EVERY_S, steps: Steps | None = None) -> threading.Thread:
    """Look at the pool's builds every ``every`` seconds until ``stopping``, as long as the device's node lives."""
    def loop() -> None:
        while not stopping():
            slept = 0.0
            while slept < every and not stopping():
                time.sleep(0.25)
                slept += 0.25
            try:
                if not stopping():
                    update_once(state, steps=steps)
            except (OSError, ValueError, subprocess.SubprocessError, updates.UpdateError):
                continue

    thread = threading.Thread(target=loop, daemon=True, name="build-update")
    thread.start()
    return thread

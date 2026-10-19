"""Keeps the test queue moving: ends background test jobs that have gone quiet or run far too long."""

from __future__ import annotations

import os
import re
import signal
import time
from collections.abc import Callable
from pathlib import Path

import psutil
import testjobs
import testreuse_store as storage

QUIET_S = 900.0
MAX_AGE_S = 5400.0
GRACE_S = 5.0
OLD_QUIET_S = 300.0
NODE_MAX_AGE_S = 3600.0
TEST_NODE = re.compile(r"/pytest-of-[^/]+/.*/poolhouse-node run --state ")
INIT_NAMES = frozenset({"launchd", "systemd", "init"})


def _log_age(jobs: testjobs.Jobs, job: str, now: float) -> float:
    log = jobs.path(job) / "log"
    try:
        return now - log.stat().st_mtime
    except OSError:
        return 0.0


def hung(jobs: testjobs.Jobs, job: str, now: float) -> str:
    """Why ``job`` is hung (empty when it is not): it has no owner, its log is quiet, or it has run too long."""
    status = jobs.read(job)
    if status.get("state") not in ("queued", "running"):
        return ""
    spec = jobs.read(job, "spec.json")
    owner = spec.get("owner") if isinstance(spec, dict) else None
    if not (isinstance(owner, dict) and isinstance(owner.get("id"), str) and owner["id"]):
        return "it has no owner"
    created = spec.get("created", status.get("at", now)) if isinstance(spec, dict) else now
    quiet = _log_age(jobs, job, now)
    if now - float(created) > MAX_AGE_S and quiet > OLD_QUIET_S:
        return f"older than {MAX_AGE_S / 60:.0f} minutes and quiet for {quiet / 60:.0f}"
    return f"no output for {quiet / 60:.0f} minutes" if quiet > QUIET_S else ""


def end(pid: int, started: float, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Stop the process group of ``pid`` when it still is the process that started at ``started``; True when it is gone."""
    if not storage.alive(pid, started):
        return True
    leader = os.getpgid(pid) == pid
    for sig, wait in ((signal.SIGTERM, GRACE_S), (signal.SIGKILL, GRACE_S)):
        if not storage.alive(pid, started):
            break
        try:
            os.killpg(pid, sig) if leader else os.kill(pid, sig)
        except ProcessLookupError:
            break
        sleep(wait)
    return not storage.alive(pid, started)


def sweep(jobs: testjobs.Jobs, now: float | None = None, say: Callable[[str], None] = print,
          sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """End every hung job and record it as failed with the reason; returns the ids ended."""
    now = time.time() if now is None else now
    ended = []
    for job in jobs.ids():
        try:
            why = hung(jobs, job, now)
            if not why:
                continue
            status = jobs.read(job)
            gone = end(int(status.get("pid", 0)), float(status.get("started", 0)), sleep)
        except (testjobs.Refused, OSError, ValueError, TypeError) as error:
            say(f"queue doctor: skipped job {job}: {type(error).__name__}: {error}"[:200])
            continue
        if gone:
            jobs.write(job, "status.json", {**status, "state": "failed", "exit": 3, "finished": now,
                                            "detail": f"ended by the queue doctor: {why}"})
            say(f"queue doctor: ended job {job}: {why}")
            ended.append(job)
    return ended


def jobs_of(root: Path) -> testjobs.Jobs:
    """The background jobs of the project at ``root``."""
    from poolhouse.activity import reuse
    from poolhouse.workspace import testruns
    return testjobs.Jobs(reuse.reuse_base() / testruns.scope(root))


def supervisors(proc: psutil.Process) -> list[psutil.Process]:
    """The node supervisor that started ``proc``, when it has one."""
    parent = proc.parent()
    return [parent] if parent and parent.pid > 1 and "poolhouse.node_launch" in " ".join(parent.cmdline()) else []


def orphaned(proc: psutil.Process) -> bool:
    """True when nothing but init and node supervisors is left above ``proc``: the run that started it is gone."""
    live = [a for a in proc.parents() if a.pid > 1 and a.name() not in INIT_NAMES and "poolhouse.node_launch" not in " ".join(a.cmdline())]
    return not live


def reap_nodes(now: float | None = None, say: Callable[[str], None] = print, only: frozenset[int] | None = None) -> list[int]:
    """End old test nodes (and their supervisors) whose pytest run is gone; the pids in ``only`` are ended without that check. Returns the pids ended."""
    now = time.time() if now is None else now
    ended = []
    for proc in psutil.process_iter(["pid", "cmdline", "create_time"]):
        try:
            if only is not None and proc.info["pid"] not in only:
                continue
            line = " ".join(proc.info["cmdline"] or [])
            if not TEST_NODE.search(line) or now - proc.info["create_time"] < NODE_MAX_AGE_S:
                continue
            if only is None and not orphaned(proc):
                continue
            doomed = [proc, *supervisors(proc)]
            for victim in doomed:
                victim.terminate()
            _, alive = psutil.wait_procs(doomed, timeout=GRACE_S)
            for victim in alive:
                victim.kill()
            ended.append(proc.pid)
            say(f"queue doctor: ended leaked test node {proc.pid}")
        except (psutil.Error, OSError):
            continue
    return ended


def mend(root: Path, say: Callable[[str], None] = print, now: float | None = None) -> list[str]:
    """One pass of the doctor over the project's jobs; a failure of the pass never stops the caller."""
    try:
        ended = sweep(jobs_of(root), now, say)
        reap_nodes(now, say)
        return ended
    except (OSError, ValueError, TypeError, ImportError) as error:
        say(f"queue doctor: pass failed: {type(error).__name__}: {error}"[:200])
        return []

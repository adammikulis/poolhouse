"""Reports invalid jobs and ends jobs whose runner is gone; reports quiet, old or long jobs."""

from __future__ import annotations

import os
import re
import signal
import time
from collections.abc import Callable
from pathlib import Path

import psutil
import queue_notice
import testjobs
import testreuse_store as storage

QUIET_ALERT_S = 900.0
ESTIMATE_ALERT_S = 1200.0
OVERRUN_ALERT_S = 1200.0
ADMIT_NOTICE_S = 600.0
PASS_EVERY_S = 10.0
AGE_ALERT_S = 5400.0
GRACE_S = 5.0
NODE_MAX_AGE_S = 3600.0
TEST_NODE = re.compile(r"/pytest-of-[^/]+/.*/poolhouse-node run --state ")
INIT_NAMES = frozenset({"launchd", "systemd", "init"})


def _log_age(jobs: testjobs.Jobs, job: str, now: float) -> float:
    log = jobs.path(job) / "log"
    try:
        return now - log.stat().st_mtime
    except OSError:
        return 0.0


def person(spec: object) -> bool:
    """Whether a person's terminal started the job; the doctor never ends one."""
    owner = spec.get("owner") if isinstance(spec, dict) else None
    return isinstance(owner, dict) and owner.get("source") == "terminal"


def duration(seconds: float) -> str:
    """Seconds written as "90 s", "45 min" or "2.8 h"."""
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.0f} min" if seconds < 7200 else f"{seconds / 3600:.1f} h"


def estimate_text(status: dict) -> str:
    """The job's estimate and the workers it is expected to take."""
    return f"estimated {duration(float(status['estimate_s']))} on {status.get('workers', '?')} workers"


def estimate_cause(spec: dict, status: dict) -> str:
    """Why an estimate is too long: the estimate and, for a whole-suite run, who runs those."""
    argv, root = spec.get("argv"), spec.get("root")
    whole = isinstance(argv, list) and isinstance(root, str) and testjobs.is_full([str(a) for a in argv], Path(root))
    return f"{estimate_text(status)}: " + ("a full suite; GitHub runs those" if whole else "too long to hold the queue; run fewer files")


def over_estimate(spec: dict, status: dict, now: float) -> str:
    """Why an agent's job is over its estimate (empty when it is not): estimated over `ESTIMATE_ALERT_S`, or running `OVERRUN_ALERT_S` past its estimate."""
    estimate = status.get("estimate_s")
    if not isinstance(estimate, (int, float)):
        return ""
    if estimate > ESTIMATE_ALERT_S:
        return f"{estimate_cause(spec, status)} (a notice only; ESTIMATE_ALERT_S = {ESTIMATE_ALERT_S:.0f} s in scripts/queue_doctor.py)"
    ran = now - float(status.get("at", now))
    if ran > estimate + OVERRUN_ALERT_S:
        return (f"ran {duration(ran)}, {duration(ran - estimate)} past its estimate of {duration(estimate)} "
                f"(a notice only; OVERRUN_ALERT_S = {OVERRUN_ALERT_S:.0f} s in scripts/queue_doctor.py)")
    return ""


def alerts(jobs: testjobs.Jobs, job: str, now: float) -> list[tuple[str, str]]:
    """The notices an agent's running job calls for, as (kind, text): quiet, old or past its estimate. Nothing is ended for them."""
    status, spec = jobs.read(job), jobs.read(job, "spec.json")
    if status.get("state") != "running" or not isinstance(spec, dict) or person(spec):
        return []
    found = []
    if (quiet := _log_age(jobs, job, now)) > QUIET_ALERT_S:
        found.append(("quiet", f"has printed nothing for {duration(quiet)} (a notice only; QUIET_ALERT_S = {QUIET_ALERT_S:.0f} s in scripts/queue_doctor.py)"))
    created = spec.get("created", status.get("at", now))
    if now - float(created) > AGE_ALERT_S:
        found.append(("old", f"has run for {duration(now - float(created))} (a notice only; AGE_ALERT_S = {AGE_ALERT_S:.0f} s in scripts/queue_doctor.py)"))
    if why := over_estimate(spec, status, now):
        found.append(("estimate", why))
    return found


def hung(jobs: testjobs.Jobs, job: str, now: float) -> str:
    """Why ``job`` is hung (empty when it is not): it has no owner, or its runner process is gone."""
    status = jobs.read(job)
    if status.get("state") not in ("queued", "running"):
        return ""
    spec = jobs.read(job, "spec.json")
    owner = spec.get("owner") if isinstance(spec, dict) else None
    if not testjobs.valid_owner(owner):
        return "it has no owner"
    pid = int(status.get("pid", 0))
    if pid and not storage.alive(pid, float(status.get("started", 0))):
        return "its runner process is gone"
    return ""


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
          sleep: Callable[[float], None] = time.sleep, tell: Callable[..., bool] = queue_notice.post) -> list[str]:
    """End every hung job, record it as failed with the reason and tell its owner and the board; tell the owner of a quiet, old or long job once. Returns the ids ended."""
    now = time.time() if now is None else now
    ended = []
    for job in jobs.ids():
        try:
            why = hung(jobs, job, now)
            if not why:
                for kind, text in alerts(jobs, job, now):
                    tell(jobs, job, kind, text, say)
                continue
            if why == "it has no owner":
                say(f"queue doctor: skipped job {job}: {why}")
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
            tell(jobs, job, "ended", f"ended by the queue doctor: {why}", say)
            ended.append(job)
    return ended


def admitted(jobs: testjobs.Jobs, job: str, estimate_s: float, workers: int, say: Callable[[str], None] = print) -> None:
    """Record the running job's estimate and tell the board about an admission over `ADMIT_NOTICE_S`; nothing is ended."""
    status = {**jobs.read(job), "estimate_s": estimate_s, "workers": workers}
    jobs.write(job, "status.json", status)
    if not person(jobs.read(job, "spec.json")) and estimate_s > ADMIT_NOTICE_S:
        queue_notice.post(jobs, job, "admitted", f"admitted: {estimate_text(status)}", say)


def check(root: Path, say: Callable[[str], None] = print, now: float | None = None) -> list[str]:
    """One doctor pass for the project at ``root``, at most one every ``PASS_EVERY_S``; a failure of the pass never stops the caller."""
    try:
        jobs = jobs_of(root)
        stamp = jobs.folder / "doctor-pass"
        moment = time.time() if now is None else now
        if stamp.exists() and moment - stamp.stat().st_mtime < PASS_EVERY_S:
            return []
        jobs.folder.mkdir(parents=True, exist_ok=True)
        stamp.touch()
        os.utime(stamp, (moment, moment))
    except (OSError, ValueError, TypeError, ImportError) as error:
        say(f"queue doctor: pass failed: {type(error).__name__}: {error}"[:200])
        return []
    return mend(root, say, now)


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
    from poolhouse.serve.process import readable

    now = time.time() if now is None else now
    ended = []
    for proc, info in readable(["pid", "cmdline", "create_time"]):
        try:
            if only is not None and info["pid"] not in only:
                continue
            line = " ".join(info["cmdline"] or [])
            if not TEST_NODE.search(line) or now - (info["create_time"] or now) < NODE_MAX_AGE_S:
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

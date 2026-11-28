"""Keeping a process for every job that has not ended, with Poolhouse's own means: a detached process per job, started when it
is scheduled, and started again by the daemon (which autostart runs at login) or by any `poolhouse schedule` command when it is
gone, as after a restart of the computer. A job is scheduled only once its process holds it (`begin`). Nothing is written
outside Poolhouse's state, and nobody is asked anything.
"""

from __future__ import annotations

import os
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from poolhouse import caller, home, jobs, runtime, schedule_record as record
from poolhouse.log import warn
from poolhouse.schedule import who

__all__ = ["AGAIN_S", "HELD_WAIT_S", "KEEP_S", "KEPT", "NOTICE_AGAIN_S", "NOTICE_DAYS", "adopted", "begin", "ensure",
           "environment", "handover", "keep", "runs_jobs", "start", "stored", "taken"]

KEEP_S = 30.0
AGAIN_S = 30.0
"""A job's process is not started twice within this many seconds, so one that dies at once is not started in a loop."""
HELD_WAIT_S = 90.0
"""How long a started process has to take its job."""
NOTICE_DAYS = 7
"""How long after a job ended a notice that could not be sent is still tried."""
NOTICE_AGAIN_S = 600.0
"""How long between two tries of one notice."""
TEMPORARY = ("TMPDIR", "TMP", "TEMP")
KEPT = ("HOME", "PATH", "USER", "LOGNAME", "SHELL", *TEMPORARY, "LANG", "LC_ALL", "LC_CTYPE", "TZ", "XDG_CACHE_HOME",
        "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "SYSTEMROOT", "SystemRoot", "SystemDrive", "WINDIR", "USERPROFILE",
        "USERNAME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PATHEXT", "COMSPEC", "ProgramData", "ProgramFiles",
        "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
"""The variables a job's process keeps (`environment`; the process drops every other one as it starts): where the account and
its model cache are. Nothing that says who scheduled it, no session's token, and nothing that changes what code a program loads."""
OWN = ("HOME", "USERPROFILE", home.ROOT_ENV, "PYTHONPATH", "PYTHONUNBUFFERED", *TEMPORARY)
"""The variables a job's process takes from whoever started it, not from when the job was scheduled: the account, where this
process found the job, its code and a temporary folder that is there now."""


def _paths(value: str) -> str:
    """The folders of a search path a later run may use: ones that are there, named in full, not under the temporary folder and
    not writable by everybody, so nothing that can write only those can put a program where a step will find it."""
    temporary = Path(tempfile.gettempdir()).resolve()
    out = []
    for each in value.split(os.pathsep):
        if not each or not Path(each).is_absolute():
            continue
        found = Path(each).resolve()
        if found == temporary or temporary in found.parents or not found.is_dir():
            continue
        if os.name == "posix" and found.stat().st_mode & stat.S_IWOTH:
            continue
        out.append(each)
    return os.pathsep.join(out)


def environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment of a job's process and of its steps, made from nothing but `KEPT` and where Poolhouse keeps its state."""
    found = os.environ if source is None else source
    names = (*KEPT, home.ROOT_ENV, home.CACHE_ENV, *home.OVERRIDES.values())
    out = {name: found[name] for name in names if found.get(name)}
    if "PATH" in out:
        out["PATH"] = _paths(out["PATH"])
    out["PYTHONUNBUFFERED"] = "1"
    return out


def stored(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """What a job keeps of the environment it is scheduled in: `environment` without what is its process's own (`OWN`)."""
    return {name: value for name, value in environment(source).items() if name not in OWN}


def adopted(kept: Mapping[str, str], here: Mapping[str, str]) -> dict[str, str]:
    """The environment a job's process and its steps run in: the one ``kept`` when it was scheduled, filtered again, with this
    process's own variables (``here``, `OWN`). Whoever started the process again has no say in the rest."""
    if not kept:
        return dict(here)
    out = {name: here[name] for name in OWN if here.get(name)}
    out.update({name: value for name, value in environment(kept).items() if name not in OWN})
    return out


def runs_jobs(prefix: Path | None) -> bool:
    """Whether the installed runtime at ``prefix`` has scheduled jobs, so it can hold one."""
    return prefix is not None and any(any(Path(prefix).glob(f"{where}/poolhouse/schedule/cli.py"))
                                      for where in ("lib/python*/site-packages", "Lib/site-packages"))


def start(job: str) -> None:
    """Start the process of ``job``, owned by no terminal or app: on the selected runtime when that has scheduled jobs, else
    as this process runs."""
    where = record.folder(job)
    (where / "spawned").touch(mode=0o600)
    chosen = runtime.available()
    jobs.detach("poolhouse.schedule.cli", ["hold", job], log=where / "runner.log",
                same_interpreter=chosen is None or not runs_jobs(chosen.prefix), disclaimed=True)


def taken(job: str, *, former: int = 0) -> bool:
    """Wait, `HELD_WAIT_S` at most, until a process other than ``former`` holds ``job`` and has said so in its record, or the job
    has ended; whether it came to that."""
    until = time.monotonic() + HELD_WAIT_S
    while True:
        found = record.load(job)
        if found is not None:
            pid = (found.get("runner") or {}).get("pid")
            if found["state"] in record.ENDED or (pid and pid != former and record.alive(job)):
                return True
        if time.monotonic() >= until:
            return False
        time.sleep(0.2)


def _last_said(job: str) -> str:
    """The last line the job's process printed."""
    try:
        lines = (record.folder(job) / "runner.log").read_text(encoding="utf-8", errors="replace").split("\n")
    except OSError:
        return "it left no log"
    return next((line.strip()[-300:] for line in reversed(lines) if line.strip()), "it printed nothing")


def begin(job: str) -> None:
    """`start` the process of a job just scheduled and wait until it holds it. When none does, the job ends failed, so nothing
    starts it later, and `ValueError` says why."""
    try:
        start(job)
        if taken(job):
            return
        why = f"no process took it within {HELD_WAIT_S:g}s: {_last_said(job)}"
    except (OSError, ValueError, RuntimeError) as error:
        why = f"no process took it: it could not be started: {error}"

    def fail(found: dict) -> None:
        if found["state"] == "waiting" and not (found.get("runner") and record.alive(job)):
            now = time.time()
            found.update(state="failed", why=why, ended=now, notice={"sent": now, "error": ""})

    if record.update(job, fail)["state"] == "failed":
        caller.record("schedule.end", job=job, state="failed", why=why)
        raise ValueError(f"{job} was not scheduled: {why}")


def handover(job: str) -> bool:
    """Start the successor of this process, which has let ``job`` go; whether the successor took it."""
    try:
        start(job)
    except (OSError, ValueError, RuntimeError) as why:
        warn(f"the successor of scheduled job {job} did not start: {why}")
        return False
    return taken(job, former=os.getpid())


def _due(one: dict, now: float) -> bool:
    if one.get("refused"):
        return False
    if one["state"] in record.ENDED:
        notice = one.get("notice") or {}
        return (not notice.get("sent") and now - float(notice.get("tried") or 0) >= NOTICE_AGAIN_S
                and now - float(one.get("ended") or 0) < NOTICE_DAYS * 86400)
    return True


def ensure(*, begin: Callable[[str], None] | None = None, now: float | None = None) -> list[str]:
    """Start a process for every job that needs one and has none; the jobs started. Does nothing for a caller that may not
    schedule (in a sandbox, or under an unattended agent): its process would run the job with that caller's limits."""
    if who.refused_here():
        return []
    moment = time.time() if now is None else now
    started = []
    for one in record.jobs():
        if not _due(one, moment) or record.alive(one["id"]):
            continue
        mark = record.folder(one["id"]) / "spawned"
        if mark.exists() and moment - mark.stat().st_mtime < AGAIN_S:
            continue
        try:
            (begin or start)(one["id"])
        except (OSError, ValueError, RuntimeError) as why:
            warn(f"the process of scheduled job {one['id']} did not start: {why}")
            continue
        started.append(one["id"])
    return started


def keep(stop: threading.Event, every: float = KEEP_S) -> None:
    """`ensure` now and every ``every`` seconds until ``stop`` is set: what the daemon runs."""
    while True:
        try:
            ensure()
        except (OSError, ValueError, RuntimeError) as why:
            warn(f"scheduled jobs could not be looked after: {why}")
        if stop.wait(every):
            return

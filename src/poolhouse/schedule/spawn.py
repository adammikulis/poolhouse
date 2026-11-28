"""Keeping a process for every job that has not ended, with Poolhouse's own means: a detached process per job, started when it
is scheduled, and started again by the daemon (which autostart runs at login) or by any `poolhouse schedule` command when it is
gone, as after a restart of the computer. Nothing is written outside Poolhouse's state, and nobody is asked anything.
"""

from __future__ import annotations

import os
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from poolhouse import home, jobs, runtime, schedule_record as record
from poolhouse.log import warn
from poolhouse.schedule import who

__all__ = ["AGAIN_S", "KEEP_S", "KEPT", "NOTICE_AGAIN_S", "NOTICE_DAYS", "ensure", "environment", "keep", "start"]

KEEP_S = 30.0
AGAIN_S = 30.0
"""A job's process is not started twice within this many seconds, so one that dies at once is not started in a loop."""
NOTICE_DAYS = 7
"""How long after a job ended a notice that could not be sent is still tried."""
NOTICE_AGAIN_S = 600.0
"""How long between two tries of one notice."""
KEPT = ("HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "XDG_CACHE_HOME",
        "HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "SYSTEMROOT", "SystemRoot", "SystemDrive", "WINDIR", "USERPROFILE",
        "USERNAME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PATHEXT", "COMSPEC", "ProgramData", "ProgramFiles",
        "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
"""The variables a job's process keeps (`environment`; the process drops every other one as it starts): where the account and
its model cache are. Nothing that says who scheduled it, no session's token, and nothing that changes what code a program loads."""


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


def start(job: str) -> None:
    """Start the process of ``job``, owned by no terminal or app, on the selected runtime."""
    where = record.folder(job)
    (where / "spawned").touch(mode=0o600)
    jobs.detach("poolhouse.schedule.cli", ["hold", job], log=where / "runner.log",
                same_interpreter=runtime.available() is None, disclaimed=True)


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

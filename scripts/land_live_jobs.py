"""Read-only test job protection during integration cleanup."""
from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import psutil
import testjobs

from poolhouse import lock
from poolhouse.activity import reuse
from poolhouse.workspace import testruns


def record(path: Path) -> dict:
    """Read a bounded regular job record."""
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
        raise ValueError("invalid job record")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("invalid job record")
    return value


def processes_use(paths: set[Path]) -> bool:
    """Whether a same-user process uses a candidate tree or cannot be inspected."""
    if not hasattr(os, "getuid"):
        return True
    for process in psutil.process_iter():
        try:
            if process.uids().real != os.getuid():
                continue
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
            cwd = Path(process.cwd()).resolve()
            if any(cwd == path or path in cwd.parents for path in paths):
                return True
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError, ValueError):
            return True
    return False


def jobs_use(jobs: testjobs.Jobs, paths: set[Path]) -> bool:
    """Whether active or uncertain maintained jobs protect cleanup candidates."""
    ids = jobs.ids()
    if len(ids) > 4096:
        return True
    for job in ids:
        spec = record(jobs.path(job) / "spec.json")
        raw = spec.get("root")
        if spec.get("id") != job or not isinstance(raw, str) or not Path(raw).is_absolute():
            return True
        path = Path(raw).resolve()
        if path not in paths:
            continue
        status = record(jobs.path(job) / "status.json")
        if status.get("state") not in testjobs.TERMINAL:
            return True
        pid, started = status.get("pid"), status.get("started")
        if type(pid) is not int or pid <= 0 or type(started) not in (int, float) or started <= 0:
            return True
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - started) < 1 and process.status() != psutil.STATUS_ZOMBIE:
                return True
        except psutil.NoSuchProcess:
            pass
    return processes_use(paths)


@contextmanager
def cleanup_allowed(root: Path, paths: set[Path]) -> Iterator[bool]:
    """Hold the job admission lock while permitting only unprotected cleanup."""
    jobs = testjobs.Jobs(reuse.reuse_base() / testruns.scope(root))
    try:
        jobs.folder.mkdir(parents=True, exist_ok=True)
        handle = (jobs.folder / "lock").open("a")
    except OSError:
        yield False
        return
    with handle:
        try:
            acquired = lock.take(handle)
        except OSError:
            acquired = False
        if not acquired:
            yield False
            return
        try:
            try:
                safe = not jobs_use(jobs, paths)
            except (OSError, ValueError, TypeError, psutil.Error, testjobs.Refused):
                safe = False
            yield safe
        finally:
            lock.release(handle)

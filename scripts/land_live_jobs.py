"""Read-only test job protection during integration cleanup."""
from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from itertools import islice
from pathlib import Path

import psutil
import testjobs

from poolhouse import lock
from poolhouse.activity import reuse
from poolhouse.workspace import testruns


def record(path: Path) -> dict:
    """Read a bounded regular job record."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError("invalid job record")
        payload = handle.read(65537)
        if len(payload) > 65536:
            raise ValueError("invalid job record")
        value = json.loads(payload)
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
            raw = [process.cwd(), *process.cmdline()]
            if len(raw) > 4096 or any(len(arg) > 65536 for arg in raw):
                return True
            for arg in raw:
                value = arg.partition("=")[2] if arg.startswith("-") and "=" in arg else arg
                if not Path(value).is_absolute():
                    continue
                candidate = Path(value).resolve()
                if any(candidate == path or path in candidate.parents for path in paths):
                    return True
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError, ValueError):
            return True
    return False


def jobs_use(jobs: testjobs.Jobs, paths: set[Path]) -> bool:
    """Whether active or uncertain maintained jobs protect cleanup candidates."""
    entries = list(islice(jobs.folder.iterdir(), 4098)) if jobs.folder.exists() else []
    if len(entries) > 4097:
        return True
    for entry in entries:
        if entry.name == "lock":
            continue
        if not stat.S_ISDIR(entry.lstat().st_mode):
            return True
        job = entry.name
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

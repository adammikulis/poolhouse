"""Private bounded integer-map files and serialized updates."""

from __future__ import annotations

import json
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from poolhouse import lock
from poolhouse.files import write_json
from poolhouse.private_path import problem, redirected
from poolhouse.windows_private import restrict, validate


def valid(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(origin, str) and type(number) is int and number >= 0
                                          for origin, number in value.items())


def read(target: Path) -> dict[str, dict[str, int]]:
    why = problem(target)
    if why == "missing":
        return {}
    if why:
        raise ValueError(f"private counter {why}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    handle = os.open(target, flags)
    with os.fdopen(handle, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or redirected(info):
            raise ValueError("private counter must be a regular file")
        if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError("private counter must be private and owned")
        current = target.lstat()
        if redirected(current) or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("private counter changed while opening")
        text = stream.read((1 << 20) + 1)
    if len(text) > 1 << 20:
        raise ValueError("private counter exceeds 1 MiB")
    saved = json.loads(text)
    if not isinstance(saved, dict) or any(not isinstance(key, str) or not valid(value)
                                          for key, value in saved.items()):
        raise ValueError("saved private counter is malformed")
    return saved


@contextmanager
def locked(target: Path):
    path = target.with_suffix(".lock")
    why = problem(path)
    if why not in {"", "missing"}:
        raise ValueError(f"private counter lock {why}")
    handle = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or redirected(info):
            raise ValueError("private counter lock must be a regular file")
        if os.name == "nt":
            restrict(path)
            validate(path)
        elif info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("private counter lock must be private and owned")
        deadline = time.monotonic() + 3.0
        while not lock.take(handle):
            if time.monotonic() >= deadline:
                raise OSError("private counter is busy; retry the update")
            time.sleep(0.01)
        try:
            yield
        finally:
            lock.release(handle)
    finally:
        os.close(handle)


def write(target: Path, value: dict[str, dict[str, int]]) -> None:
    """Atomically save a validated bounded integer map under an already-held lock."""
    if not isinstance(value, dict) or any(not isinstance(key, str) or not valid(item) for key, item in value.items()):
        raise ValueError("saved private counter is malformed")
    if len(json.dumps(value).encode()) > 1 << 20:
        raise ValueError("private counter exceeds 1 MiB")
    write_json(target, value)
    if os.name == "nt":
        restrict(target)
    else:
        target.chmod(0o600)

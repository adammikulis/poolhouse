"""Cross-process revocation epochs; this file carries no execution grants."""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager, suppress
from pathlib import Path

from poolhouse import lock
from poolhouse.files import promote

NAME = "studio-authority-epoch.json"
HEX = re.compile(r"[0-9a-f]{64}\Z")
REASONS = {"start", "leave", "forget", "invitation-revoked", "member-revoked", "security-reset"}


def _private(info: os.stat_result) -> None:
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077
            or (hasattr(os, "getuid") and info.st_uid != os.getuid())):
        raise PermissionError("Studio authority must be an account-private regular file.")


@contextmanager
def directory(root: Path, *, create: bool = False):
    root = Path(root).absolute()
    if ".." in root.parts:
        raise ValueError("Authority directories cannot contain parent traversal.")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_mode & 0o022 or (hasattr(os, "getuid") and info.st_uid != os.getuid()):
            raise PermissionError("Studio authority directory must belong to this account.")
        yield fd
    finally:
        os.close(fd)


def _read(fd: int, *, absent: bool = False) -> dict:
    try:
        handle = os.open(NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        if absent:
            return {"version": 1, "pools": {}}
        raise PermissionError("Studio authority has not been established.") from None
    with os.fdopen(handle, "r") as source:
        _private(os.fstat(source.fileno()))
        try:
            value = json.load(source)
        except (ValueError, UnicodeError):
            raise PermissionError("Studio authority is unreadable.") from None
    if (not isinstance(value, dict) or set(value) != {"version", "pools"}
            or type(value["version"]) is not int or value["version"] != 1 or not isinstance(value["pools"], dict)):
        raise PermissionError("Studio authority has an unsupported format.")
    for pool, row in value["pools"].items():
        if (not isinstance(pool, str) or not HEX.fullmatch(pool) or not isinstance(row, dict)
                or set(row) != {"epoch", "active", "reason"} or not isinstance(row["epoch"], str)
                or not HEX.fullmatch(row["epoch"]) or type(row["active"]) is not bool or not isinstance(row["reason"], str) or row["reason"] not in REASONS):
            raise PermissionError("Studio authority contains an invalid epoch.")
    return value


def read(root: Path, pool: str) -> str:
    """Current reads never bootstrap or reactivate authority."""
    with directory(root) as fd:
        row = _read(fd)["pools"].get(pool)
    if row is None or not row["active"]:
        raise PermissionError("Studio execution authority is revoked or missing.")
    return row["epoch"]


@contextmanager
def changing(root: Path):
    """Hold a descriptor-bound private lock without opening the grants database."""
    with directory(root, create=True) as fd:
        handle = os.open(NAME + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        acquired = False
        try:
            _private(os.fstat(handle))
            while not lock.take(handle):
                time.sleep(0.005)
            acquired = True
            yield fd, _read(fd, absent=True)
        finally:
            if acquired:
                lock.release(handle)
            os.close(handle)


def _write(fd: int, value: dict) -> None:
    draft = NAME + "." + secrets.token_hex(16)
    handle = os.open(draft, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    try:
        with os.fdopen(handle, "w") as target:
            json.dump(value, target, sort_keys=True, separators=(",", ":"), allow_nan=False)
            target.flush()
            os.fsync(target.fileno())
        promote(draft, NAME, directory_fd=fd)
        os.fsync(fd)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(draft, dir_fd=fd)


def activate(fd: int, value: dict, pool: str) -> str:
    """Only the explicit owner Start transaction calls this function."""
    if not HEX.fullmatch(pool):
        raise ValueError("Invalid authority pool identity.")
    row = value["pools"].get(pool)
    if row is None or not row["active"]:
        row = {"epoch": secrets.token_hex(32), "active": True, "reason": "start"}
        value["pools"][pool] = row
        _write(fd, value)
    return row["epoch"]


def invalidate(root: Path, pools: list[str], reason: str) -> None:
    """Explicit credential changes revoke before modifying their own state."""
    if reason not in REASONS - {"start"} or any(not HEX.fullmatch(pool) for pool in pools):
        raise ValueError("Invalid authority revocation.")
    with changing(root) as (fd, value):
        for pool in pools:
            existing = value["pools"].get(pool)
            if existing and not existing["active"]:
                continue
            value["pools"][pool] = {"epoch": secrets.token_hex(32), "active": False, "reason": reason}
        _write(fd, value)

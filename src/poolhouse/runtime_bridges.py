"""Exact owned command bridges into Poolhouse's canonical launcher directory."""

from __future__ import annotations

import os
import shlex
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from poolhouse import home, runtime, runtime_git, runtime_launchers
from poolhouse.files import writing


def declared(checkout: Path, commit: str) -> dict[str, str]:
    """Return the console scripts declared by an exact source commit."""
    config = tomllib.loads(runtime_git.git(checkout, "show", f"{commit}:pyproject.toml"))
    return runtime_launchers.validate_entries(config["project"]["scripts"])


def planned(checkout: Path, commit: str, launchers: Path | None) -> dict[Path, bytes]:
    """Return the exact shared command bridges for a canonical deployment."""
    if (launchers != runtime_launchers.directory() or os.name == "nt"
            or runtime.directory().parent.parent != home.account_roots()[0]):
        return {}
    directory = home.account_roots()[0].parent / ".local" / "bin"
    runtime.confine_tests(directory)
    runtime.plain(directory)
    if not directory.is_dir() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o022:
        raise OSError("the command folder must be an owned directory")
    return {directory / name: ("#!/bin/sh\nexec " + shlex.quote(str(launchers / name)) + ' "$@"\n').encode()
            for name in declared(checkout, commit)}


def _read(path: Path) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    runtime_launchers.owned(path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022
                or info.st_size > 65536):
            raise OSError("the command bridge must be an owned bounded regular file")
        body = stream.read(65537)
    if len(body) > 65536:
        raise OSError("the command bridge exceeds its size limit")
    return body


def changes(expected: Mapping[Path, bytes]) -> list[Path]:
    """Return only exact command bridges whose contents need changing."""
    return [path for path, body in expected.items()
            if _read(path) != body or not path.stat().st_mode & stat.S_IXUSR]


@dataclass(frozen=True, slots=True)
class Previous:
    """The contents and identity of one claimed command bridge."""

    body: bytes | None
    mode: int = 0o700
    identity: tuple[int, int, int] | None = None


def snapshot(expected: Mapping[Path, bytes], claimed: frozenset[Path]) -> dict[Path, Previous]:
    """Snapshot only changed exact bridge files covered by this deployment's claims."""
    result = {}
    for path in changes(expected):
        if path not in claimed:
            raise OSError("a command bridge changed while deployment was preparing; retry the deployment")
        body = _read(path)
        info = path.stat() if body is not None else None
        result[path] = Previous(body, stat.S_IMODE(info.st_mode) if info else 0o700,
                                (info.st_dev, info.st_ino, info.st_mode) if info else None)
    return result


def install(expected: Mapping[Path, bytes], previous: Mapping[Path, Previous]) -> None:
    """Prepare exact claimed bridges without rewriting already valid commands."""
    for path, old in previous.items():
        body = _read(path)
        info = path.stat() if body is not None else None
        identity = (info.st_dev, info.st_ino, info.st_mode) if info else None
        if body != old.body or identity != old.identity:
            raise OSError("a command bridge changed during deployment")
        with writing(path) as temporary:
            temporary.write_bytes(expected[path])
            temporary.chmod(0o700)
    if changes(expected):
        raise OSError("a command bridge changed during deployment")


def restore(previous: Mapping[Path, Previous], expected: Mapping[Path, bytes]) -> None:
    """Restore only exact bridge files changed by this deployment."""
    for path, old in previous.items():
        current = _read(path)
        if current == old.body and (current is None or stat.S_IMODE(path.stat().st_mode) == old.mode):
            continue
        if current not in (expected[path], old.body):
            raise OSError("a command bridge changed before deployment could restore it")
        if old.body is None:
            path.unlink()
        else:
            with writing(path) as temporary:
                temporary.write_bytes(old.body)
                temporary.chmod(old.mode)

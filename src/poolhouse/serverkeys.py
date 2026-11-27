"""The API key each leased model server was started with, and the lookup that finds it by URL."""

from __future__ import annotations

import os
import secrets
import time
import urllib.parse
from pathlib import Path

from poolhouse import home
from poolhouse.files import read_json, write_json
from poolhouse.lock import only_one, pid_alive

__all__ = ["LOOPBACK", "bind", "for_url", "forget", "issue", "key_file", "write_key_file"]

LOOPBACK = ("127.0.0.1", "localhost", "::1")
STARTING_S = 600


def _file() -> Path:
    return home.state("server-keys.json")


def _entries() -> dict[str, dict]:
    found = read_json(_file(), {})
    return found if isinstance(found, dict) else {}


def _live(entry: object) -> bool:
    if not isinstance(entry, dict) or not isinstance(entry.get("key"), str):
        return False
    if entry.get("pid") == 0:
        return time.time() - float(entry.get("at", 0)) < STARTING_S
    pid = entry.get("pid")
    return isinstance(pid, int) and pid_alive(pid)


def _save(entries: dict[str, dict]) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, entries)
    path.chmod(0o600)


def _locked():
    """The file's own lock, so two processes issuing or binding keys never overwrite each other's."""
    return only_one(_file().with_suffix(".lock"), wait=True, timeout=30.0, announce=lambda _line: None)


def key_file(port: int) -> Path:
    """Where the key of the server on ``port`` is handed to it, for `--api-key-file`: a command line is public to every
    process of the machine, a file of mode 0600 in a directory of mode 0700 is not."""
    return home.state("server-keys.d", f"{int(port)}.key")


def write_key_file(port: int, key: str) -> Path:
    """`key_file` of ``port`` holding ``key``, created private."""
    path = key_file(port)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.unlink(missing_ok=True)   # never write through a link or into a file somebody else made
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(key + "\n")
    path.chmod(0o600)
    return path


def issue(port: int) -> str:
    """A new key for the server about to start on ``port``, recorded for `for_url`."""
    key = secrets.token_hex(32)
    with _locked():
        found = _entries()
        entries = {p: e for p, e in found.items() if _live(e)}
        for gone in (p for p in set(found) - set(entries) if p.isdigit()):   # servers that ended without being stopped by us
            key_file(int(gone)).unlink(missing_ok=True)
        entries[str(int(port))] = {"key": key, "pid": 0, "at": time.time()}
        _save(entries)
    return key


def forget(port: int) -> None:
    """Drop the key of the server on ``port`` and the file it was handed over in, once that server has ended or never came up.
    The key of a live process bound to the port stays: a start that failed because another server holds the port is not the end of it."""
    with _locked():
        entries = _entries()
        entry = entries.get(str(int(port)))
        if isinstance(entry, dict) and isinstance(entry.get("pid"), int) and pid_alive(entry["pid"]):
            return
        if entries.pop(str(int(port)), None) is not None:
            _save(entries)
        key_file(port).unlink(missing_ok=True)


def bind(port: int, pid: int) -> None:
    """Tie the key issued for ``port`` to the server process ``pid``."""
    with _locked():
        entries = _entries()
        if str(int(port)) in entries:
            entries[str(int(port))]["pid"] = int(pid)
            _save(entries)


def for_url(url: str) -> str:
    """The key of the leased server at a loopback ``url``, or ``""`` when none is running there."""
    parts = urllib.parse.urlsplit(url)
    if parts.hostname not in LOOPBACK:
        return ""
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return ""
    entry = _entries().get(str(port))
    # Only a key bound to its server's process is sent: no health probe made before the listener is checked carries one.
    return entry["key"] if isinstance(entry, dict) and entry.get("pid") and _live(entry) else ""

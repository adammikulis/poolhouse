"""The API key each leased model server was started with, and the lookup that finds it by URL.

A key is only in a file of mode 0600 whose name is made when the key is, in a folder of mode 0700: a coding tool reads files a project's own config
names and sends them where that config says, and a project cannot name a file it cannot know. What lists the servers holds no key.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import time
import urllib.parse
from pathlib import Path

import psutil

from poolhouse import home
from poolhouse.files import read_json, write_json
from poolhouse.lock import only_one, pid_alive
from poolhouse.ownership import START_TOLERANCE_S, listens_on, recorded_servers, same_process

__all__ = ["LOOPBACK", "bind", "bound", "for_url", "forget", "held", "issue", "key_file", "unproved", "unproxied", "write_key_file"]

LOOPBACK = ("127.0.0.1", "localhost", "::1")
STARTING_S = 600


def _born(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).create_time())
    except (psutil.Error, OSError, ValueError):
        return None


def _file() -> Path:
    return home.state("server-keys.json")


def _entries() -> dict[str, dict]:
    found = read_json(_file(), {})
    return found if isinstance(found, dict) else {}


def _live(entry: object) -> bool:
    if not isinstance(entry, dict):
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


def _files(port: int) -> list[Path]:
    return list(home.state("server-keys.d").glob(f"{int(port)}-*.key"))


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def key_file(port: int) -> Path:
    """Where the key of the server on ``port`` is, for `--api-key-file`: a command line is public to every process of the machine, a file of mode
    0600 in a folder of mode 0700 is not. A path that does not exist where the port has no key."""
    return next(iter(_files(port)), home.state("server-keys.d", f"{int(port)}.none"))


def _write(port: int, key: str) -> Path:
    """A new private file of ``port`` holding ``key``, in place of any it had."""
    folder = home.state("server-keys.d")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    for old in _files(port):
        old.unlink(missing_ok=True)
    path = folder / f"{int(port)}-{secrets.token_hex(16)}.key"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(key + "\n")
    path.chmod(0o600)
    return path


def write_key_file(port: int, key: str) -> Path:
    """The `key_file` of ``port`` holding ``key``: the one that already does, else a new one."""
    with _locked():
        held = key_file(port)
        return held if _read(held) == key else _write(port, key)


def issue(port: int) -> str:
    """A new key for the server about to start on ``port``, kept for `for_url`."""
    key = secrets.token_hex(32)
    with _locked():
        found = _entries()
        entries = {p: e for p, e in found.items() if _live(e)}
        for gone in (p for p in set(found) - set(entries) if p.isdigit()):   # servers that ended without being stopped by us
            for old in _files(int(gone)):
                old.unlink(missing_ok=True)
        _write(port, key)
        entries[str(int(port))] = {"pid": 0, "at": time.time()}
        _save(entries)
    return key


def forget(port: int) -> None:
    """Drop the key of the server on ``port`` and its file, once that server has ended or never came up. The key of a live process bound to the
    port stays: a start that failed because another server holds the port is not the end of it."""
    with _locked():
        entries = _entries()
        entry = entries.get(str(int(port)))
        if isinstance(entry, dict) and isinstance(entry.get("pid"), int) and pid_alive(entry["pid"]):
            return
        if entries.pop(str(int(port)), None) is not None:
            _save(entries)
        for old in _files(port):
            old.unlink(missing_ok=True)


def bind(port: int, pid: int) -> None:
    """Tie the key issued for ``port`` to the server process ``pid``, known by its number and when it started."""
    with _locked():
        entries = _entries()
        if str(int(port)) in entries:
            entries[str(int(port))].update(pid=int(pid), started=_born(int(pid)))
            _save(entries)


def held(port: int) -> bool:
    """Whether this computer holds a key bound to a running process for ``port``, whether or not it can be shown now to be that process's. A
    process that only took the number (it started at another time than the key's record, or the lease record, says, or before the key was issued)
    holds nothing."""
    entry = _entries().get(str(int(port)))
    if not (isinstance(entry, dict) and entry.get("pid") and _live(entry) and _read(key_file(port))):
        return False
    born, then, issued = _born(int(entry["pid"])), entry.get("started"), entry.get("at")
    if then is None:   # the previous build wrote none: the lease record tells when that server started, when it names the process
        record = recorded_servers().get(int(port))
        then = record.get("started") if record and record.get("pid") == entry["pid"] else None
        if born is not None and isinstance(issued, (int, float)) and born < issued - START_TOLERANCE_S:
            return False
    return born is not None and (not isinstance(then, (int, float)) or abs(born - then) <= START_TOLERANCE_S)


def bound(port: int) -> int:
    """The process the key of ``port`` is bound to, while this computer holds that key and that very process runs (its number and when it started:
    a number is used again); 0 otherwise (`unproved` says why)."""
    return _judged(port)[0]


def unproved(port: int) -> str:
    """Why the key bound for ``port`` cannot be shown to be its process's; empty when it can."""
    return _judged(port)[1]


def _judged(port: int) -> tuple[int, str]:
    entry = _entries().get(str(int(port)))
    if not (isinstance(entry, dict) and entry.get("pid") and _live(entry)):
        return 0, "no key is bound to a running process on that port"
    if not _read(key_file(port)):
        return 0, "this build holds no key file for it (a build before this one's naming kept the key elsewhere)"
    pid, then = int(entry["pid"]), entry.get("started")
    if then is None:
        why = _adopted(int(port), pid, entry.get("at"))
        return (0, why) if why else (pid, "")
    now = _born(pid)
    if isinstance(then, (int, float)) and now is not None and abs(now - then) <= START_TOLERANCE_S:
        return pid, ""
    return 0, "the process now running under that number is not the one the key was bound to"


def _adopted(port: int, pid: int, issued: object) -> str:
    """For a key the previous build bound, which wrote no start time: empty, with the start time recorded now, when ``pid`` is shown to be that
    server's process, else why it is not. It must be the process of the lease record (start time and command line), have started after the key was
    issued and before the index was last written (so before the key was bound), and listen on the port on this computer's loopback."""
    record = recorded_servers().get(port)
    if record is None or record.get("pid") != pid:
        return "no lease record names the process the key is bound to"
    if record.get("unmanaged") or record.get("pending") or not same_process(record, strict=True):
        return "the lease record does not match the process running now (its start time and command line)"
    born = _born(pid)
    try:
        written = _file().stat().st_mtime
    except OSError:
        written = 0.0
    if born is None or not isinstance(issued, (int, float)) or not issued - START_TOLERANCE_S <= born <= written + START_TOLERANCE_S:
        return "the process did not start between the issue of the key and its binding"
    if not any(listens_on(pid, port, each) for each in ("127.0.0.1", "::1")):
        return "that process does not listen on the port on this computer's loopback"
    # the proof holds without the record of it: a reader that may not write the index proves it again next time
    with contextlib.suppress(OSError, RuntimeError), _locked():
        entries = _entries()
        if isinstance(entries.get(str(port)), dict) and entries[str(port)].get("pid") == pid:
            entries[str(port)]["started"] = born
            _save(entries)
    return ""


def for_url(url: str) -> str:
    """The key of the leased server at a loopback ``url``, or ``""``: it is given only while the process it is bound to listens at the very address
    the URL names (``localhost`` names both, so both)."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return ""
    if parts.hostname not in LOOPBACK:
        return ""
    # Only a key bound to its server's process is sent: no health probe made before the listener is checked carries one.
    pid = bound(port)
    addresses = ("127.0.0.1", "::1") if parts.hostname == "localhost" else (parts.hostname,)
    return _read(key_file(port)) if pid and all(listens_on(pid, port, each) for each in addresses) else ""


def unproxied(*named: str) -> dict[str, str]:
    """``NO_PROXY`` for a program that is to reach a server of this computer: the entries of each of ``named`` (a ``NO_PROXY`` it already had) and
    this computer's own names, so that no proxy is handed a request with the server's key."""
    entries = [entry.strip() for each in (*named, *LOOPBACK) for entry in each.split(",")]
    direct = ",".join(dict.fromkeys(entry for entry in entries if entry))
    return {"NO_PROXY": direct, "no_proxy": direct}

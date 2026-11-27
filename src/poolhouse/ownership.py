"""What this computer records about the model servers it runs, and how a running process is shown to be one of them."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import psutil

from poolhouse import home
from poolhouse.lock import pid_alive

__all__ = ["START_TOLERANCE_S", "cmdline_digest", "lease_file", "listens_on", "recorded_servers", "same_process"]

START_TOLERANCE_S = 2.0


def lease_file() -> Path:
    """The file recording which model servers this machine is running."""
    return home.moved("servers.json")


def recorded_servers(state_file: Path | None = None) -> dict[int, dict]:
    """Every server in the lease file, keyed by port."""
    try:
        parsed = json.loads((state_file or lease_file()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}

    out: dict[int, dict] = {}
    for key, entry in parsed.items():
        if not isinstance(entry, dict):
            continue
        try:
            out[int(entry.get("port", key))] = entry
        except (TypeError, ValueError):
            continue
    return out


def cmdline_digest(pid: int | None) -> str | None:
    """A digest of ``pid``'s command line, or None when it is not running or will not say."""
    if not pid or not pid_alive(pid):
        return None
    try:
        argv = psutil.Process(pid).cmdline()
    except psutil.Error:
        return None
    return hashlib.sha256("\0".join(argv).encode("utf-8", "replace")).hexdigest() if argv else None


def same_process(entry: dict, *, strict: bool = False) -> bool:
    """Whether the process now holding a record's pid is the server the record was written
    for: it started when the record says and runs the command line the record hashed.

    A recorded start time or command line that no longer matches is a different process.
    One the record does not hold is not checked, unless ``strict``, which refuses a record
    that cannot prove both."""
    pid = entry.get("pid")
    if not isinstance(pid, int):
        return False
    recorded, digest = entry.get("started"), entry.get("cmdline")
    if strict and not (isinstance(recorded, (int, float)) and isinstance(digest, str)):
        return False
    if not pid_alive(pid, recorded if isinstance(recorded, (int, float)) else None, START_TOLERANCE_S):
        return False
    return not isinstance(digest, str) or cmdline_digest(pid) == digest


def listens_on(pid: int | None, port: int, address: str = "") -> bool:
    """Whether the process ``pid`` (or one of its children, for a sandbox wrapper) is alive and listens on ``port``; with ``address``
    (``127.0.0.1`` or ``::1``), on exactly that address. A listener on every address is not one on ``address``: another process can bind the one
    address beside it and take its connections.

    The check made before a key is sent to whatever answers on a port a server was started for."""
    if not pid:
        return False

    def holds(process: psutil.Process) -> bool:
        return any(c.status == psutil.CONN_LISTEN and c.laddr and c.laddr.port == port and (not address or c.laddr.ip == address)
                   for c in process.net_connections(kind="inet"))

    try:
        parent = psutil.Process(pid)
        # the children are looked for only when the process itself does not listen: finding them reads every process of the computer
        return holds(parent) or any(holds(child) for child in parent.children(recursive=True))
    except (psutil.Error, OSError, TypeError):
        return False

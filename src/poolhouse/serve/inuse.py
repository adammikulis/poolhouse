"""Whether a model server is in use, asked before one is stopped or replaced, and the record of every stop.

A server is unused only on positive proof: the file of who holds what can be read and neither it nor the asking broker names a live holder besides
the server itself, no other live process is recorded as the one that leased it, and no request is on it. What cannot be read counts as use.
Every stop and replacement is written to the activity log as ``model.stop`` (``poolhouse-log tail --kind model.stop``): who asked, from which
process, which server and why.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poolhouse import activity, home
from poolhouse.serve.process import pid_exists, request_on

__all__ = ["Asking", "held_by", "held_file", "keyed_elsewhere", "stopped", "why_in_use"]

logger = logging.getLogger(__name__)


def held_file(state_file: Path) -> Path:
    """Where the brokers of the lease registry ``state_file`` record who holds what."""
    return state_file.with_name("broker-leases.json")


def _recorded(port: int, state_file: Path) -> dict[str, tuple[int, str]] | None:
    """The holders on file for ``port``; None when the file is there and cannot be read."""
    try:
        kept = json.loads(held_file(state_file).read_text(encoding="utf-8"))
        holders = (kept.get(str(port)) or {}).get("holders") or {}
        return {str(lease): (int(who[0]), str(who[1])) for lease, who in holders.items()}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError, AttributeError, IndexError, KeyError):
        return None


@dataclass(frozen=True)
class Asking:
    """What the one asking about a stop knows: the holders it has in memory, the leases being let go now, its own process when it is giving up
    its hold, and how it tells that a process lives."""

    known: Mapping[str, tuple[int, str]] = field(default_factory=dict)
    leaving: Collection[str] = ()
    asker: int = 0
    alive: Callable[[int], bool] = pid_exists


def held_by(port: int, entry: Mapping[str, Any], state_file: Path, asking: Asking = Asking()) -> str:  # noqa: B008 - frozen
    """Who still holds the server of the record ``entry`` on ``port``, in words; empty when every record read says nobody does.
    The server's hold on itself (``poolhouse-serve up``) is not somebody using it."""
    recorded = _recorded(port, state_file)
    if recorded is None:
        return "the record of who holds it cannot be read"
    pid, owner = entry.get("pid"), entry.get("owner_pid")
    live = {lease: who for lease, who in {**recorded, **asking.known}.items()
            if lease not in asking.leaving and who[0] not in (pid, asking.asker) and asking.alive(who[0])}
    if not live and isinstance(owner, int) and owner not in (pid, asking.asker, os.getpid()) and asking.alive(owner):
        live = {str(entry.get("lease") or f"recorded-{port}"): (owner, "recorded owner")}
    return "it is still held by " + ", ".join(f"lease {lease} of pid {who}" + (f" ({label})" if label else "")
                                              for lease, (who, label) in live.items()) if live else ""


def why_in_use(port: int, entry: Mapping[str, Any], state_file: Path, asking: Asking = Asking(),  # noqa: B008 - frozen
               requests: Callable[[int, int], bool | None] = request_on) -> str:
    """Why the server of ``entry`` on ``port`` counts as in use (`held_by`, then a request on it); empty only when it is shown unused."""
    if why := held_by(port, entry, state_file, asking):
        return why
    pid = entry.get("pid")
    if not isinstance(pid, int) or not pid_exists(pid):
        return ""
    busy = requests(pid, port)
    if busy is None:
        return "whether a request is on it cannot be read"
    return "a request is in progress on it" if busy else ""


def keyed_elsewhere(row: Mapping[str, Any]) -> bool:
    """Whether the scanned server ``row`` was given its key by another Poolhouse's state folder, such as the installed one seen from a
    test or another build: it is counted and never asked anything."""
    named = str(row.get("key_file") or "")
    return bool(named) and not Path(named).resolve().is_relative_to(home.state("server-keys.d").resolve())


def stopped(port: int, pid: int | None, why: str, *, asker: str = "") -> None:
    """Write that the server ``pid`` on ``port`` was stopped: by whom (``asker``, else this process's command) and why."""
    who = asker or " ".join([Path(sys.argv[0]).name, *sys.argv[1:]])[:120]
    logger.warning("stopping the server on port %s (pid %s), asked by %s (pid %s): %s", port, pid, who, os.getpid(), why)
    activity.record("model.stop", subject=f"port:{port}", outcome="stopped", refs={"for": who},
                    meta={"port": port, "pid": pid or 0, "by": os.getpid(), "why": why})

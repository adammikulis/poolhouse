"""Reading every process on the machine without a process that exits mid-scan ending the scan.

psutil reads each process's attributes lazily. A process that exits (or that macOS will not
show) while the scan is under way raises from inside the reading: `psutil.NoSuchProcess`,
`psutil.AccessDenied`, an `OSError`, or on macOS a bare `SystemError` ("proc_cmdline ...
exception set") with the `PermissionError` chained. `psutil.process_iter(attrs)` reads the
attributes inside its own generator, so such an error escapes the caller's loop and, the
generator being spent, cannot be resumed; this module reads one process at a time instead.
"""

from __future__ import annotations

import logging
from typing import Any

import psutil

logger = logging.getLogger(__name__)

#: what reading one process can raise when it is gone, forbidden or half-torn-down
UNREADABLE = (psutil.Error, OSError, SystemError)


def readable(attrs: list[str], *, uncertain: bool = False) -> list[tuple[Any, dict]]:
    """Each process with its ``attrs`` read; one that vanished is left out.

    A process that exists but could not be read is counted and logged at debug; with
    ``uncertain`` it is kept with every attribute ``None``, for a caller (an idle proof)
    that must not mistake "unreadable" for "absent".
    """
    found: list[tuple[Any, dict]] = []
    unreadable = 0
    for proc in psutil.process_iter():
        try:
            found.append((proc, proc.as_dict(attrs)))
        except psutil.NoSuchProcess:
            continue
        except UNREADABLE:
            unreadable += 1
            if uncertain:
                found.append((proc, dict.fromkeys(attrs)))
    if unreadable:
        logger.debug("%d process(es) could not be read during the scan", unreadable)
    return found


def connections(proc: Any, kind: str = "inet") -> list[Any]:
    """``proc``'s sockets, or none when the process vanished or may not be inspected."""
    try:
        return list(proc.net_connections(kind=kind))
    except UNREADABLE:
        return []

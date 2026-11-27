"""Which process listens on a port."""

from __future__ import annotations

import psutil

__all__ = ["listens_on"]


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

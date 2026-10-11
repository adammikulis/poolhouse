"""Which terminal a process runs in, so a program that registers a session of its own gets one identity per terminal."""

from __future__ import annotations

import os

import psutil

__all__ = ["terminal_id"]


def _leads_a_terminal(process: psutil.Process) -> bool:
    return process.terminal() is not None and os.getsid(process.pid) == process.pid


def terminal_id() -> str:
    """The terminal this process runs in, as the id and start of the shell that leads it; with no terminal, of the process at
    the top of its parents (the process id where there is none): the node registers a session again only from the lineage
    it came from, and a terminal is one."""
    try:
        me = psutil.Process()
        chain = [process for process in (me, *me.parents()) if process.pid > 1]
        top = next((process for process in chain if _leads_a_terminal(process)), chain[-1])
        return f"{top.pid}-{int(top.create_time())}"
    except (AttributeError, IndexError, OSError, psutil.Error):
        return str(os.getpid())

"""Test nodes left running by a run that is gone: a supervisor and its node whose state folder is a test's short temporary one."""

from __future__ import annotations

import os
import re
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path

TEMP_STATES = ("/tmp/ml", "/private/tmp/ml")  # noqa: S108 - where the node fixtures keep their state folders
"""Where the node fixtures make a node's state folder (`tests/node_kit.py`)."""
MIN_AGE_S = 3600.0
"""A test node that has run this long belongs to a run that is no longer there."""
KINDS = (("-m", "poolhouse.node_launch", "supervise"), ("run",))
"""The words after the program: a test supervisor runs a module of Python and a test node is the binary."""
STATE = re.compile(r"--state (\S+)")
ETIME = re.compile(r"^(?:(?:(\d+)-)?(\d+):)?(\d+):(\d+)$")


def seconds(etime: str) -> float:
    """The seconds in a `ps` elapsed time (`[[dd-]hh:]mm:ss`); 0 when it does not parse."""
    found = ETIME.match(etime.strip())
    if not found:
        return 0.0
    days, hours, minutes, secs = (int(part or 0) for part in found.groups())
    return float(((days * 24 + hours) * 60 + minutes) * 60 + secs)


def processes() -> list[tuple[int, float, str]]:
    """This account's processes as (pid, seconds running, command line)."""
    done = subprocess.run(["ps", "-U", str(os.getuid()), "-o", "pid=,etime=,command="], capture_output=True, text=True, check=False)
    rows = []
    for line in done.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit():
            rows.append((int(parts[0]), seconds(parts[1]), parts[2]))
    return rows


def stale(rows: list[tuple[int, float, str]], prefixes: tuple[str, ...] = TEMP_STATES, min_age: float = MIN_AGE_S) -> list[int]:
    """The pids of test supervisors and nodes older than ``min_age`` whose state folder starts with one of ``prefixes``, supervisors first."""
    found = []
    for pid, age, command in rows:
        words = command.split()
        state = STATE.search(command)
        kind = 0 if words[1:4] == list(KINDS[0]) and Path(words[0]).name.startswith("python") else (
            1 if words[1:2] == list(KINDS[1]) and Path(words[0]).name == "poolhouse-node" else -1)
        if kind >= 0 and state and age >= min_age and state.group(1).startswith(prefixes):
            found.append((kind, pid))
    return [pid for _, pid in sorted(found)]


def reap(prefixes: tuple[str, ...] = TEMP_STATES, min_age: float = MIN_AGE_S, say: Callable[[str], None] = lambda _text: None,
         listing: Callable[[], list[tuple[int, float, str]]] = processes) -> int:
    """Stop the stale test nodes with SIGTERM and say how many; a process that is already gone is not an error."""
    try:
        pids = stale(listing(), prefixes, min_age)
    except OSError:
        return 0
    stopped = 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            stopped += 1
        except OSError:
            continue
    if stopped:
        say(f"test: stopped {stopped} test node process(es) left running by runs that are gone")
    return stopped

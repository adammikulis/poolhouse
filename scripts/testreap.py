"""Test nodes left running by a run that is gone: a supervisor and its node whose state folder is a test's short temporary one."""

from __future__ import annotations

import os
import re
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import psutil

from poolhouse.lock import pid_alive

TEMP_STATES = ("/tmp/ml", "/private/tmp/ml")  # noqa: S108 - where the node fixtures keep their state folders
"""Where the node fixtures make a node's state folder (`tests/node_kit.py`)."""
MIN_AGE_S = 3600.0
"""How long a test node runs before it is stopped when no process is named as its owner."""
START_SLACK_S = 3.0
"""How far two readings of a process's start time may differ and still be one process."""
SUPERVISOR = ("-m", "poolhouse.node_launch", "supervise")
"""The words after the program: a test supervisor runs a module of Python and a test node is the binary."""
STATE = re.compile(r"--state (\S+)")
OWNER = re.compile(r"--parent-pid (\d+)")


class Proc(NamedTuple):
    """One listed process: its pid, its parent's pid, when it started (epoch seconds) and its command line."""

    pid: int
    parent: int
    started: float
    command: str


def processes() -> list[Proc]:
    """The processes of this account whose command line can be read."""
    return [Proc(info["pid"], info["ppid"], info["create_time"], " ".join(info["cmdline"]))
            for info in (process.info for process in psutil.process_iter(["pid", "ppid", "create_time", "cmdline"])) if info["cmdline"]]


def kind(command: str) -> int:
    """0 for a test supervisor, 1 for a test node, -1 for any other command line."""
    words = command.split()
    if words[1:4] == list(SUPERVISOR) and Path(words[0]).name.startswith("python"):
        return 0
    return 1 if words[1:2] == ["run"] and Path(words[0]).name == "poolhouse-node" else -1


def owner_gone(pid: int, started: float) -> bool:
    """Whether the process ``pid`` has exited, or is a process that started after ``started`` and took its number."""
    if not pid_alive(pid):
        return True
    try:
        return psutil.Process(pid).create_time() > started + START_SLACK_S
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False


def in_test_folder(row: Proc, prefixes: tuple[str, ...]) -> int:
    """The kind of ``row`` when its state folder starts with one of ``prefixes``, else -1."""
    state = STATE.search(row.command)
    return kind(row.command) if state and state.group(1).startswith(prefixes) else -1


def stale(rows: list[Proc], prefixes: tuple[str, ...] = TEMP_STATES, min_age: float = MIN_AGE_S, now: float | None = None,
          gone: Callable[[int, float], bool] = owner_gone) -> list[int]:
    """The pids of test supervisors and nodes whose state folder starts with one of ``prefixes`` and whose run is gone, supervisors first.

    A supervisor started with ``--parent-pid`` is stale once that process has exited. A supervisor without one is stale after
    ``min_age`` seconds. A node is stale when its supervisor is, or when its parent has exited and it has run ``min_age`` seconds."""
    now = time.time() if now is None else now
    supervisors = {}
    for row in rows:
        watched = OWNER.search(row.command)
        if in_test_folder(row, prefixes) == 0 and (gone(int(watched.group(1)), row.started) if watched else now - row.started >= min_age):
            supervisors[row.pid] = row
    nodes = [row.pid for row in rows if in_test_folder(row, prefixes) == 1 and (
        row.parent in supervisors or (now - row.started >= min_age and (row.parent <= 1 or gone(row.parent, row.started))))]
    return [*sorted(supervisors), *sorted(nodes)]


def unchanged(row: Proc) -> bool:
    """Whether ``row.pid`` is still the process that was listed: it started when the row says and runs the same command line."""
    try:
        live = psutil.Process(row.pid)
        return abs(live.create_time() - row.started) <= START_SLACK_S and " ".join(live.cmdline()).split() == row.command.split()
    except psutil.Error:
        return False


def reap(prefixes: tuple[str, ...] = TEMP_STATES, min_age: float = MIN_AGE_S, say: Callable[[str], None] = lambda _text: None,
         listing: Callable[[], list[Proc]] = processes, recheck: Callable[[Proc], bool] = unchanged) -> int:
    """Stop the stale test nodes with SIGTERM and say how many; a process that is gone or has become another is left alone."""
    try:
        rows = listing()
    except OSError:
        return 0
    listed = {row.pid: row for row in rows}
    stopped = 0
    for pid in stale(rows, prefixes, min_age):
        if not recheck(listed[pid]):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped += 1
        except OSError:
            continue
    if stopped:
        say(f"test: stopped {stopped} test node process(es) left running by runs that are gone")
    return stopped

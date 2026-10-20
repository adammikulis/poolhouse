"""Test processes left running by a run that is gone: a supervisor and its node whose state folder is a test's short temporary one, and a daemon whose root is under pytest's temporary folder."""

from __future__ import annotations

import json
import os
import re
import signal
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import psutil

from poolhouse.lock import pid_alive

TEMP_STATES = ("/tmp/ml", "/private/tmp/ml")  # noqa: S108 - where the node fixtures keep their state folders
"""Where the node fixtures make a node's state folder (`tests/node_kit.py`)."""
DAEMON_ROOTS = (str(Path(tempfile.gettempdir()).resolve() / "pytest-of-"),)
"""Where pytest keeps the folders a test's daemon is given as its root: ``pytest-of-<user>/pytest-<n>/...``."""
MIN_AGE_S = 3600.0
"""How long a test node runs before it is stopped when no process is named as its owner."""
START_SLACK_S = 3.0
"""How far two readings of a process's start time may differ and still be one process."""
SUPERVISOR = ("-m", "poolhouse.node_launch", "supervise")
"""The words after the program: a test supervisor runs a module of Python and a test node is the binary."""
STATE = re.compile(r"--state (\S+)")
OWNER = re.compile(r"--parent-pid (\d+)")
DAEMON = ("-m", "poolhouse.cli.daemon")
"""The words after the program of a daemon."""
ROOT = re.compile(r"--root (\S+)")


class Proc(NamedTuple):
    """One listed process: its pid, its parent's pid, when it started (epoch seconds) and its command line."""

    pid: int
    parent: int
    started: float
    command: str


def processes() -> list[Proc]:
    """The processes of this account whose command line can be read; one that cannot be read is left out."""
    rows = []
    for process in psutil.process_iter():
        try:
            if command := process.cmdline():
                rows.append(Proc(process.pid, process.ppid(), process.create_time(), " ".join(command)))
        except (psutil.Error, OSError, SystemError):  # SystemError: macOS, a process exiting mid-read
            continue
    return rows


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


def daemon_environment(pid: int) -> dict[str, str]:
    """The environment of the process ``pid``; empty when it cannot be read."""
    try:
        return psutil.Process(pid).environ()
    except (psutil.Error, OSError, SystemError):
        return {}


def owner_pid(pid: int) -> int | None:
    """The pid of the test process the daemon ``pid`` was started under (its ``POOLHOUSE_NODE_PARENT_PID``), or ``None``."""
    from poolhouse.node_supervise import PARENT_ENV

    named = daemon_environment(pid).get(PARENT_ENV, "")
    return int(named) if named.isdigit() else None


def recorded_pid(root: str) -> int | None:
    """The pid in the run record ``start_daemon`` wrote under ``root``, or ``None`` when there is none (pytest removes the folders of old runs)."""
    from poolhouse.fleet.join import STARTED_FILE

    try:
        held: Any = json.loads((Path(root) / STARTED_FILE).read_text())
        return int(held["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def daemon_root(row: Proc, roots: tuple[str, ...]) -> str | None:
    """The ``--root`` of ``row`` when it is a daemon (a Python running the daemon module) whose root starts with one of ``roots``."""
    words = row.command.split()
    found = ROOT.search(row.command)
    if words[1:3] != list(DAEMON) or not Path(words[0]).name.startswith("python") or not found:
        return None
    return found.group(1) if found.group(1).startswith(roots) else None


class Readers(NamedTuple):
    """What `stale_daemons` asks about a process; tests replace them."""

    gone: Callable[[int, float], bool] = owner_gone
    owner_of: Callable[[int], int | None] = owner_pid
    recorded: Callable[[str], int | None] = recorded_pid


def stale_daemons(rows: list[Proc], roots: tuple[str, ...] = DAEMON_ROOTS, min_age: float = MIN_AGE_S, now: float | None = None,
                  readers: Readers | None = None) -> list[int]:
    """The pids of daemons whose root starts with one of ``roots``, whose run record under that root, when there is one, names them and whose run is gone.

    A daemon started under a test process names it in its environment and is stale once that process has exited. One that names
    none is stale after ``min_age`` seconds once its parent has exited."""
    now = time.time() if now is None else now
    readers = readers or Readers()
    found = []
    for row in rows:
        root = daemon_root(row, roots)
        if root is None or readers.recorded(root) not in (row.pid, None):
            continue
        owner = readers.owner_of(row.pid)
        if readers.gone(owner, row.started) if owner is not None else (
                now - row.started >= min_age and (row.parent <= 1 or readers.gone(row.parent, row.started))):
            found.append(row.pid)
    return sorted(found)


def unchanged(row: Proc) -> bool:
    """Whether ``row.pid`` is still the process that was listed: it started when the row says and runs the same command line."""
    try:
        live = psutil.Process(row.pid)
        return abs(live.create_time() - row.started) <= START_SLACK_S and " ".join(live.cmdline()).split() == row.command.split()
    except psutil.Error:
        return False


def stop(pid: int) -> bool:
    """Send SIGTERM to the process ``pid``, and to its process group when it leads one; whether the process was signalled."""
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def stop_listed(pids: list[int], listed: dict[int, Proc], recheck: Callable[[Proc], bool]) -> int:
    """Stop the processes of ``pids`` that are still the ones listed; how many were signalled."""
    return sum(1 for pid in pids if recheck(listed[pid]) and stop(pid))


def reap(prefixes: tuple[str, ...] = TEMP_STATES, min_age: float = MIN_AGE_S, say: Callable[[str], None] = lambda _text: None,
         listing: Callable[[], list[Proc]] = processes, recheck: Callable[[Proc], bool] = unchanged) -> int:
    """Stop the stale test nodes with SIGTERM and say how many; a process that is gone or has become another is left alone."""
    try:
        rows = listing()
    except (psutil.Error, OSError, SystemError):
        return 0
    stopped = stop_listed(stale(rows, prefixes, min_age), {row.pid: row for row in rows}, recheck)
    if stopped:
        say(f"test: stopped {stopped} test node process(es) left running by runs that are gone")
    return stopped


def reap_daemons(roots: tuple[str, ...] = DAEMON_ROOTS, min_age: float = MIN_AGE_S, say: Callable[[str], None] = lambda _text: None,
                 listing: Callable[[], list[Proc]] = processes, recheck: Callable[[Proc], bool] = unchanged) -> int:
    """Stop the stale test daemons with SIGTERM and say how many; a process that is gone or has become another is left alone."""
    try:
        rows = listing()
    except (psutil.Error, OSError, SystemError):
        return 0
    stopped = stop_listed(stale_daemons(rows, roots, min_age), {row.pid: row for row in rows}, recheck)
    if stopped:
        say(f"test: stopped {stopped} test daemon process(es) left running by runs that are gone")
    return stopped


def reap_all(say: Callable[[str], None] = lambda _text: None) -> int:
    """Stop the stale test nodes and daemons; how many were stopped."""
    return reap(say=say) + reap_daemons(say=say)

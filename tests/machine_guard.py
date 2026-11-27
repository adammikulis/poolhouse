"""A test run cannot see or signal a model server it did not start (`tests/test_machine_guard.py`).

2026-11-27: teardowns that ended every server their broker listed stopped the machine's own llama-server three times, mid-request. A broker
under test scans the whole machine, so its list holds servers no test started.

A process is the run's own when it is this process, descends from it, or carries the run's mark in its environment, which every process the run
starts inherits. macOS does not show the environment of its own programs; one of those whose parent has gone counts when it began during the
run and is no model server. ``os.kill`` and ``os.killpg``, which psutil's ``terminate`` and
``kill`` go through, refuse any other with ``PermissionError`` and the test that asked fails. A process scan inside a test does not list a
llama-server that is not the run's own; `machine_servers` reads them all, for the rule that a real-model test never loads beside one.
`conftest.py` imports `signals_refused`, which installs both.
"""

from __future__ import annotations

import contextlib
import errno
import inspect
import os
import uuid
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import psutil
import pytest

from poolhouse.serve import process

MARK = "POOLHOUSE_TEST_RUN"
RUN = os.environ.setdefault(MARK, uuid.uuid4().hex)
BEGAN = psutil.Process().create_time()
REFUSED: list[str] = []
"""The signals refused since the running test began, one line each."""

_kill = getattr(os.kill, "unguarded", os.kill)
_killpg = getattr(getattr(os, "killpg", None), "unguarded", getattr(os, "killpg", None))
_readable = getattr(process.readable, "unguarded", process.readable)
_whole_machine: list[bool] = []


def own(pid: int) -> bool:
    """Whether ``pid`` is a process of this test run; a process that is gone or has exited counts, since a signal does it nothing."""
    if pid == os.getpid():
        return True
    try:
        one = psutil.Process(pid)
        if one.status() == psutil.STATUS_ZOMBIE or any(parent.pid == os.getpid() for parent in one.parents()):
            return True
    except psutil.NoSuchProcess:
        return True
    except (psutil.Error, OSError):
        pass
    environment: Mapping[str, str] = {}
    with contextlib.suppress(psutil.Error, OSError, SystemError):  # SystemError: macOS, a process exiting mid-read
        environment = psutil.Process(pid).environ()
    if environment:
        return environment.get(MARK) == RUN
    with contextlib.suppress(psutil.Error, OSError, SystemError):
        one = psutil.Process(pid)
        return one.create_time() >= BEGAN and not _a_server({"name": one.name(), "cmdline": one.cmdline()})
    return False


def _group(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def own_group(group: int) -> bool:
    """Whether every process in the group ``group`` (0: this process's) is of this run; the test runner's own group holds the runner,
    which no test started."""
    wanted = group or os.getpgrp()
    return all(own(pid) for pid in psutil.pids() if _group(pid) == wanted)


def _refuse(what: str) -> None:
    where = next((f"{f.filename}:{f.lineno}" for f in inspect.stack()[1:] if f"{os.sep}tests{os.sep}" in f.filename
                  and f.filename != __file__), "<outside the tests>")
    REFUSED.append(f"{what} at {where}")
    raise PermissionError(errno.EPERM, f"a test may not {what}: this run did not start it")


def kill(pid: int, number: int) -> None:
    """``os.kill`` for this run's own processes; signal 0, which only asks whether one exists, goes to any."""
    if number != 0:
        if pid == -1:
            _refuse("signal every process")
        elif pid <= 0 and hasattr(os, "getpgrp") and not own_group(-pid):   # Windows has no process groups to name
            _refuse(f"signal the process group {-pid}")
        elif pid > 0 and not own(pid):
            _refuse(f"signal the process {pid}")
    _kill(pid, number)


def killpg(group: int, number: int) -> None:
    """``os.killpg`` for this run's own process groups."""
    if number != 0 and not own_group(group):
        _refuse(f"signal the process group {group}")
    _killpg(group, number)


def _a_server(info: Mapping[str, Any]) -> bool:
    argv = info.get("cmdline") or []
    head = Path(argv[0]).name if argv else str(info.get("name") or "")
    return "llama-server" in head or "llama-server" in str(info.get("name") or "")


def readable(attrs: list[str], **how: Any) -> list[tuple[Any, dict]]:
    """`poolhouse.serve.process.readable` without the llama-servers this run did not start; a test's own stand-in rows pass."""
    found = _readable(attrs, **how)
    if _whole_machine or "cmdline" not in attrs:
        return found
    return [(one, info) for one, info in found
            if not (isinstance(one, psutil.Process) and _a_server(info) and not own(one.pid))]


def machine_servers() -> list[dict]:
    """Every llama-server on the machine, read only: a real-model test never loads a model beside one."""
    _whole_machine.append(True)
    try:
        return process.every_server()
    finally:
        _whole_machine.pop()


def stop_started(servers: Iterable[Mapping[str, Any]]) -> None:
    """End the servers in a broker's report that the broker under test started. One it only lists, such as an unmanaged server, is somebody
    else's and is left running."""
    for one in servers:
        if one["pid"] and one["ours"] and not one["unmanaged"]:
            process.kill_process_tree(one["pid"])


def install() -> None:
    kill.unguarded, killpg.unguarded, readable.unguarded = _kill, _killpg, _readable  # type: ignore[attr-defined]
    os.kill = kill
    if _killpg is not None:
        os.killpg = killpg
    process.readable = readable


install()


@pytest.fixture(autouse=True)
def signals_refused() -> Iterator[list[str]]:
    """The signals this test was refused; it fails when any was, also one the code under test swallowed."""
    REFUSED.clear()
    yield REFUSED
    if REFUSED:
        pytest.fail("a test signalled a process this run did not start:\n" + "\n".join(REFUSED), pytrace=False)

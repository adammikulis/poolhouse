"""A test run cannot hurt the Poolhouse the person is running (`tests/test_machine_guard.py`).

2026-11-27: teardowns that ended every server their broker listed stopped the machine's own llama-server three times, mid-request. A broker
under test scans the whole machine, so its list holds servers no test started.

Protected, and nothing else: the machine's model servers, and the services of the installed Poolhouse (a program under the account's state or
cache folder). A process this run started, which descends from it or carries the run's mark in its environment, is never protected. A
llama-server run from a temporary folder is a test's stand-in. ``os.kill`` and ``os.killpg``, which psutil's ``terminate`` and ``kill`` go
through, refuse a protected process with ``PermissionError`` and the test that asked fails; every other signal goes through, also to confined
and orphaned processes. A process scan inside a test does not list a protected llama-server, so no broker under test asks it anything;
`machine_servers` reads them all, for the rule that a real-model test never loads beside one. `installed_ports` are the ports the protected
processes listen on, which `conftest.py` refuses a connection to. `conftest.py` imports `signals_refused`, which installs all this.
"""

from __future__ import annotations

import contextlib
import errno
import functools
import inspect
import os
import tempfile
import uuid
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import psutil
import pytest

from poolhouse import home, sandbox
from poolhouse.serve import process

MARK = "POOLHOUSE_TEST_RUN"
RUN = os.environ.setdefault(MARK, uuid.uuid4().hex)
INSTALLED: tuple[Path, ...] = home.account_roots()
"""The account's own state and cache folders: what runs from them is the installed Poolhouse."""
TEMPORARY = (str(Path(tempfile.gettempdir()).resolve()), "/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
REFUSED: list[str] = []
"""The signals refused since the running test began, one line each."""

_kill = getattr(os.kill, "unguarded", os.kill)
_killpg = getattr(getattr(os, "killpg", None), "unguarded", getattr(os, "killpg", None))
_readable = getattr(process.readable, "unguarded", process.readable)
_whole_machine: list[bool] = []


def own(pid: int) -> bool:
    """Whether ``pid`` is shown to be a process of this test run."""
    if pid == os.getpid():
        return True
    with contextlib.suppress(psutil.Error, OSError):
        if any(parent.pid == os.getpid() for parent in psutil.Process(pid).parents()):
            return True
    with contextlib.suppress(psutil.Error, OSError, SystemError):  # SystemError: macOS, a process exiting mid-read
        return psutil.Process(pid).environ().get(MARK) == RUN
    return False


def _a_server(info: Mapping[str, Any]) -> bool:
    argv = info.get("cmdline") or []
    head = Path(argv[0]).name if argv else str(info.get("name") or "")
    return "llama-server" in head or "llama-server" in str(info.get("name") or "")


def protected(pid: int) -> bool:
    """Whether ``pid`` is part of the person's running setup: a model server or an installed Poolhouse service this run did not start."""
    try:
        one = psutil.Process(pid)
        argv = one.cmdline()
        name = one.name()
    except (psutil.Error, OSError, SystemError):
        return False
    program = argv[0] if argv else ""
    installed = any(program.startswith(f"{root}{os.sep}") for root in INSTALLED)
    server = _a_server({"cmdline": argv, "name": name}) and not program.startswith(TEMPORARY)
    return (installed or server) and not own(pid)


def installed_ports() -> frozenset[int]:
    """The ports the protected processes listen on, read once for the run."""
    return _ports(INSTALLED)


@functools.cache
def _ports(_roots: tuple[Path, ...]) -> frozenset[int]:
    found: set[int] = set()
    for pid in running():
        if protected(pid):
            with contextlib.suppress(psutil.Error, OSError):
                found |= {c.laddr.port for c in psutil.Process(pid).net_connections(kind="inet") if c.status == psutil.CONN_LISTEN}
    return frozenset(found)


def _group(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def _refuse(what: str) -> None:
    where = next((f"{f.filename}:{f.lineno}" for f in inspect.stack()[1:] if f"{os.sep}tests{os.sep}" in f.filename
                  and f.filename != __file__), "<outside the tests>")
    REFUSED.append(f"{what} at {where}")
    raise PermissionError(errno.EPERM, f"a test may not {what}: it is part of the Poolhouse this machine is running")


def _holds_protected(group: int) -> bool:
    """Whether a process in the group ``group`` (0: this process's) is protected."""
    if not hasattr(os, "getpgrp"):  # Windows has no process groups to name
        return False
    wanted = group or os.getpgrp()
    return any(protected(pid) for pid in running() if _group(pid) == wanted)


def running() -> list[int]:
    """Every pid of the computer; none in a coding box, which may not list processes and whose profile signals none outside itself."""
    try:
        return psutil.pids()
    except PermissionError:
        if not sandbox.confined():
            raise
        return []


def kill(pid: int, number: int) -> None:
    """``os.kill`` for anything but the person's running setup; signal 0, which only asks whether a process exists, goes to any."""
    if number != 0:
        if pid == -1:
            _refuse("signal every process")
        elif pid <= 0 and _holds_protected(-pid):
            _refuse(f"signal the process group {-pid}")
        elif pid > 0 and protected(pid):
            _refuse(f"signal the process {pid}")
    _kill(pid, number)


def killpg(group: int, number: int) -> None:
    """``os.killpg`` for any group that holds nothing of the person's running setup."""
    if number != 0 and _holds_protected(group):
        _refuse(f"signal the process group {group}")
    _killpg(group, number)


def readable(attrs: list[str], **how: Any) -> list[tuple[Any, dict]]:
    """`poolhouse.serve.process.readable` without the machine's own llama-servers; a test's stand-ins and rows pass."""
    found = _readable(attrs, **how)
    if _whole_machine or "cmdline" not in attrs:
        return found
    return [(one, info) for one, info in found
            if not (isinstance(one, psutil.Process) and _a_server(info) and protected(one.pid))]


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
        pytest.fail("a test signalled the Poolhouse this machine is running:\n" + "\n".join(REFUSED), pytrace=False)

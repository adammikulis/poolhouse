"""A model server on Windows: a job object bounds it, its key folder is the owner's alone, and the missing sandbox is said.

The first tests stand the Win32 calls in with a recorder and run anywhere; the last runs a real job on Windows, in
.github/workflows/windows-app.yml. No model is loaded: the "server" is a Python process that sleeps.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from poolhouse import platform, serverkeys, win32
from poolhouse.fleet import serving_routes
from poolhouse.serve import confined, status_cli

SLEEP = [sys.executable, "-c", "import time; time.sleep(60)"]


class Kernel:
    """The job calls, recorded."""

    def __init__(self, assigned: bool = True) -> None:
        self.assigned, self.limits, self.joined, self.closed = assigned, [], [], []

    def CreateJobObjectW(self, attributes, name):
        return 71

    def SetInformationJobObject(self, job, kind, info, size):
        held = info._obj
        self.limits.append((held.BasicLimitInformation.LimitFlags, held.JobMemoryLimit))
        return 1

    def OpenProcess(self, access, inherit, pid):
        return 72

    def AssignProcessToJobObject(self, job, process):
        self.joined.append((job, process))
        return int(self.assigned)

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return 1


@pytest.fixture
def kernel(monkeypatch):
    made = Kernel()
    monkeypatch.setattr(platform, "is_windows", lambda: True)
    monkeypatch.setattr(platform, "process_group_kwargs", lambda: {})
    monkeypatch.setattr(win32, "_k32", lambda: made)
    monkeypatch.setattr(win32, "_last_error", lambda: 5)
    return made


@pytest.mark.parametrize("ends_with_us, flags", [(True, 0x2200), (False, 0x200)])
def test_a_server_on_windows_is_held_in_a_job_with_a_memory_bound(kernel, ends_with_us, flags):
    process = platform.start_server(SLEEP, ends_with_us=ends_with_us)
    try:
        assert kernel.limits == [(flags, psutil.virtual_memory().total)]
        assert kernel.joined == [(71, 72)]
    finally:
        process.kill()
        process.wait(timeout=10)


def test_a_server_that_cannot_be_bounded_is_ended_and_the_start_fails(kernel, monkeypatch):
    kernel.assigned = False
    started = []
    real = platform.subprocess.Popen
    monkeypatch.setattr(platform.subprocess, "Popen", lambda *a, **k: started.append(real(*a, **k)) or started[-1])
    with pytest.raises(OSError, match="job"):
        platform.start_server(SLEEP, ends_with_us=True)
    assert started[0].poll() is not None


def test_the_key_folder_is_made_the_owners_alone_before_a_key_is_written(monkeypatch, tmp_path):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path))
    restricted = []

    def private(path):
        restricted.append((path.name, list(path.glob("*.key"))))
        return True

    monkeypatch.setattr(serverkeys, "private_dir", private, raising=False)
    serverkeys.issue(8123)
    assert restricted and restricted[0] == ("server-keys.d", [])


def test_no_key_is_written_into_a_folder_that_cannot_be_restricted(monkeypatch, tmp_path):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path))
    monkeypatch.setattr(serverkeys, "private_dir", lambda path: False, raising=False)
    with pytest.raises(OSError, match="cannot be restricted"):
        serverkeys.issue(8123)
    assert not list((tmp_path / "server-keys.d").glob("*.key"))


@pytest.mark.parametrize("system, said", [("win32", True), ("darwin", False)])
def test_both_serving_status_surfaces_say_a_server_on_windows_is_not_sandboxed(monkeypatch, capsys, system, said):
    monkeypatch.setattr(confined, "sys", SimpleNamespace(platform=system))
    monkeypatch.setattr(status_cli.ops, "status", lambda **_: SimpleNamespace(servers=[], foreign=[], ports=[8080]))
    monkeypatch.setattr(status_cli.holding, "holds", lambda: [])
    args = argparse.Namespace(every=False, port=8080, model=None, context=None, parallel=1, json=True)
    status_cli.cmd_status(args)
    answered = []
    handler = SimpleNamespace(path="/ui/serving", method="GET", send=lambda code, body: answered.append(body),
                              ui=SimpleNamespace(serving=SimpleNamespace(live=lambda force: [])))
    serving_routes.route(handler, lambda: True)
    for told in (json.loads(capsys.readouterr().out)["sandbox"], answered[0]["sandbox"]):
        assert ("not sandboxed" in told) is said and bool(told) is said


@pytest.mark.skipif(sys.platform != "win32", reason="a job object is Windows's")
def test_on_windows_the_server_and_its_children_end_when_the_job_is_closed():
    script = f"import subprocess, time; subprocess.Popen({SLEEP!r}); time.sleep(60)"
    process = platform.start_server([sys.executable, "-c", script], ends_with_us=True)
    deadline = time.monotonic() + 30
    while not (children := psutil.Process(process.pid).children(recursive=True)) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert children
    win32.close_job(process.pid)
    _, alive = psutil.wait_procs([psutil.Process(process.pid), *children], timeout=30)
    assert not alive

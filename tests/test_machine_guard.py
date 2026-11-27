"""A test run cannot hurt the Poolhouse the person is running, and signals anything else freely (`tests/machine_guard.py`).

Each stand-in here is plumbing only, never a model: a loop that does nothing until its flag file goes, run under the name of a model server or of
an installed service, or as an ordinary program. None is a descendant of the test or carries the run's mark unless the test says so, and each
ends by itself when its flag file goes.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time

import machine_guard
import psutil
import pytest

from poolhouse.serve.process import every_server, kill_process_tree

LOOP = "import os, sys, time\nwhile os.path.exists(sys.argv[1]): time.sleep(0.2)"
"""What a stand-in runs: nothing, until the file it is given goes."""
LISTEN = ("import os, socket, sys, time\n"
          "at = socket.socket(); at.bind(('127.0.0.1', 0)); at.listen()\n"
          "open(sys.argv[1] + '.port', 'w').write(str(at.getsockname()[1]))\n"
          "while os.path.exists(sys.argv[1]): time.sleep(0.2)")
"""A stand-in that listens on a port the system picks and writes it beside its flag file."""
LEAVE = ("import os, subprocess, sys\n"
         "print(subprocess.Popen([sys.argv[2], '-c', sys.argv[3], sys.argv[1]], executable=sys.executable,\n"
         "                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=sys.argv[4] == 'alone').pid)")
"""A process that starts a stand-in under a name, says its pid and ends."""
CLOSED = {k: v for k, v in os.environ.items() if k != "POOLHOUSE_TEST_RUN"}
"""The environment of a session that does not carry the run's mark."""


def _left_behind(flag, name: str = sys.executable, program: str = LOOP, group: str = "alone") -> tuple[int, int]:
    """``(the stand-in, the process that started it and has gone)``: ``group`` "alone" gives the stand-in a session of its own,
    anything else leaves it in its starter's group."""
    starter = subprocess.Popen([sys.executable, "-c", LEAVE, str(flag), name, program, group], env=CLOSED, stdout=subprocess.PIPE,
                               text=True, start_new_session=True)
    said, _ = starter.communicate(timeout=30)
    return int(said), starter.pid


@pytest.fixture
def flag(tmp_path):
    path = tmp_path / "up"
    path.write_text("plumbing-only stand-in, not a model\n")
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def model_server(flag):
    """The pid of a stand-in for a model server the machine was already running."""
    pid, _ = _left_behind(flag, name="llama-server")
    _named(pid, "llama-server")
    return pid


def _named(pid: int, name: str) -> None:
    deadline = time.monotonic() + 10
    while _first_word(pid) != name:
        assert time.monotonic() < deadline, "the stand-in never took its name"
        time.sleep(0.05)


def _first_word(pid: int) -> str:
    with contextlib.suppress(psutil.AccessDenied):   # macOS, while the process is replacing itself
        return (psutil.Process(pid).cmdline() or [""])[0]
    return ""


def _alive(pid: int) -> bool:
    time.sleep(0.5)
    return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE


def test_a_test_cannot_signal_the_machine_s_model_server(model_server, request):
    with pytest.raises(PermissionError):
        os.kill(model_server, signal.SIGTERM)
    if hasattr(os, "killpg"):
        with pytest.raises(PermissionError):
            os.killpg(os.getpgid(model_server), signal.SIGTERM)
    kill_process_tree(model_server, grace_s=0.2)
    assert _alive(model_server)
    refused = request.getfixturevalue("signals_refused")
    assert refused, "every refused signal is listed, so the test that sent it fails"
    refused.clear()


def test_a_test_cannot_signal_an_installed_service(flag, tmp_path, monkeypatch, request):
    monkeypatch.setattr(machine_guard, "INSTALLED", (tmp_path / "installed",), raising=False)
    program = str(tmp_path / "installed" / "runtimes" / "bin" / "python")
    pid, _ = _left_behind(flag, name=program)
    _named(pid, program)
    with pytest.raises(PermissionError):
        os.kill(pid, signal.SIGTERM)
    assert _alive(pid)
    request.getfixturevalue("signals_refused").clear()


def test_a_test_s_process_scan_does_not_list_the_machine_s_model_server(model_server):
    assert model_server not in [row["pid"] for row in every_server()]


def test_a_test_ends_the_orphan_of_a_confined_session_by_pid_and_by_group(flag):
    orphan, session = _left_behind(flag, group="the session's")
    if hasattr(os, "killpg"):
        os.killpg(session, signal.SIGTERM)
    else:
        os.kill(orphan, signal.SIGTERM)
    assert not _alive(orphan)


def test_a_test_signals_any_process_that_is_no_part_of_the_running_setup(flag):
    bystander, _ = _left_behind(flag)
    os.kill(bystander, signal.SIGTERM)
    assert not _alive(bystander)


def test_a_test_still_signals_what_it_started(flag):
    child = subprocess.Popen([sys.executable, "-c", LOOP, str(flag)])
    os.kill(child.pid, signal.SIGTERM)
    assert child.wait(timeout=10) != 0


def test_a_port_a_real_server_uses_is_refused_not_only_recorded(_no_real_ports):
    with socket.socket() as one, pytest.raises(PermissionError):
        one.connect(("127.0.0.1", 8093))
    with socket.socket() as one, pytest.raises(PermissionError):
        one.connect_ex(("127.0.0.1", 8093))
    assert _no_real_ports
    _no_real_ports.clear()


def test_the_port_an_installed_service_listens_on_is_refused(flag, tmp_path, monkeypatch, request):
    monkeypatch.setattr(machine_guard, "INSTALLED", (tmp_path / "installed",), raising=False)
    program = str(tmp_path / "installed" / "bin" / "python")
    pid, _ = _left_behind(flag, name=program, program=LISTEN)
    _named(pid, program)
    said = flag.with_name(flag.name + ".port")
    deadline = time.monotonic() + 10
    while not (said.exists() and said.read_text()):
        assert time.monotonic() < deadline, "the stand-in never listened"
        time.sleep(0.05)
    refused = request.getfixturevalue("_no_real_ports")
    with socket.socket() as one, pytest.raises(PermissionError):
        one.connect(("127.0.0.1", int(said.read_text())))
    refused.clear()

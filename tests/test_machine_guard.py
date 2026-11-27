"""A test run cannot see, signal or reach a model server it did not start (`tests/machine_guard.py`).

Each "outsider" here is a plumbing-only stand-in, never a model: a loop under the name ``llama-server`` that stands for a server the machine was
already running. It is not a descendant of the test and carries no mark of the run, and it ends by itself when its flag file goes, so no test has
to signal it.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import sys
import time

import psutil
import pytest

from poolhouse.serve.process import every_server, kill_process_tree

LOOP = "import os, sys, time\nwhile os.path.exists(sys.argv[1]): time.sleep(0.2)"
"""What a stand-in runs: nothing, until the file it is given goes."""
LEAVE = ("import os, subprocess, sys\n"
         "kept = {k: v for k, v in os.environ.items() if k != sys.argv[3]}\n"
         "print(subprocess.Popen([sys.argv[2], '-c', sys.argv[4], sys.argv[1]], executable=sys.executable, env=kept,\n"
         "                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True).pid)")
"""A process that starts a stand-in under a name, without the variable it is told to drop, says its pid and ends."""


def _left_behind(flag, name: str = sys.executable, drop: str = "") -> int:
    """The pid of a stand-in whose parent has gone: in a session of its own, ending when ``flag`` goes."""
    said = subprocess.run([sys.executable, "-c", LEAVE, str(flag), name, drop, LOOP], capture_output=True, text=True, check=True,
                          start_new_session=True)
    return int(said.stdout)


@pytest.fixture
def outsider(tmp_path):
    """The pid of a stand-in server this run did not start, listed by a process scan as a llama-server."""
    flag = tmp_path / "up"
    flag.write_text("plumbing-only stand-in, not a model\n")
    pid = _left_behind(flag, name="llama-server", drop="POOLHOUSE_TEST_RUN")
    deadline = time.monotonic() + 10
    while _first_word(pid) != "llama-server":
        assert time.monotonic() < deadline, "the stand-in never took its name"
        time.sleep(0.05)
    yield pid
    flag.unlink()


def _first_word(pid: int) -> str:
    with contextlib.suppress(psutil.AccessDenied):   # macOS, while the process is replacing itself
        return (psutil.Process(pid).cmdline() or [""])[0]
    return ""


def _alive(pid: int) -> bool:
    time.sleep(0.5)
    return psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE


def test_a_test_cannot_signal_a_process_the_run_did_not_start(outsider, request):
    with pytest.raises(PermissionError):
        os.kill(outsider, signal.SIGTERM)
    if hasattr(os, "killpg"):
        with pytest.raises(PermissionError):
            os.killpg(os.getpgid(outsider), signal.SIGTERM)
    kill_process_tree(outsider, grace_s=0.2)
    assert _alive(outsider)
    signals_refused = request.getfixturevalue("signals_refused")
    assert signals_refused, "every refused signal is listed, so the test that sent it fails"
    signals_refused.clear()


def test_a_test_s_process_scan_does_not_list_a_server_the_run_did_not_start(outsider):
    assert outsider not in [row["pid"] for row in every_server()]


def test_a_test_still_signals_what_it_started_also_once_its_parent_has_gone(tmp_path):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    os.kill(child.pid, signal.SIGTERM)
    assert child.wait(timeout=10) != 0
    flag = tmp_path / "up"
    flag.touch()
    detached = _left_behind(flag)
    try:
        os.kill(detached, signal.SIGTERM)
        assert not _alive(detached)
    finally:
        flag.unlink()


def test_a_port_a_real_server_uses_is_refused_not_only_recorded(_no_real_ports):
    with socket.socket() as one, pytest.raises(PermissionError):
        one.connect(("127.0.0.1", 8093))
    with socket.socket() as one, pytest.raises(PermissionError):
        one.connect_ex(("127.0.0.1", 8093))
    assert _no_real_ports
    _no_real_ports.clear()

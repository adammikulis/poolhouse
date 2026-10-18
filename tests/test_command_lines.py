"""`command_lines` lists every process it can read and skips the ones the system will not let it read."""

from __future__ import annotations

import subprocess
import sys

import psutil
import pytest

from poolhouse.serve import process


@pytest.fixture
def sleeper():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        yield child
    finally:
        child.kill()
        child.wait()


def test_a_process_whose_command_line_the_system_refuses_is_skipped_and_the_rest_are_listed(monkeypatch, sleeper):
    real = psutil.Process.cmdline
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        def cmdline(self):
            if self.pid == sleeper.pid:
                raise PermissionError(1, "force permission denied")
            return real(self)

        monkeypatch.setattr(psutil.Process, "cmdline", cmdline)
        listed = {pid for pid, _started, _argv in process.command_lines()}
        assert other.pid in listed and sleeper.pid not in listed
    finally:
        other.kill()
        other.wait()


def test_the_state_check_lists_live_processes_past_one_the_system_refuses(monkeypatch, sleeper):
    import state_attribution

    real = psutil.Process.cmdline

    def cmdline(self):
        if self.pid == sleeper.pid:
            raise PermissionError(1, "force permission denied")
        return real(self)

    monkeypatch.delenv(state_attribution.FIXED_WRITERS, raising=False)
    monkeypatch.setattr(psutil.Process, "cmdline", cmdline)
    assert isinstance(state_attribution.live_poolhouse_processes(), list)

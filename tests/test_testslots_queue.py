"""What a queued run is told."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "testslots.py"


def load():
    spec = importlib.util.spec_from_file_location("testslots", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["testslots"] = module
    spec.loader.exec_module(module)
    return module


def test_a_queued_run_is_told_how_many_runs_it_is_queued_behind(tmp_path, monkeypatch):
    module = load()
    monkeypatch.setenv("DEV_TEST_SLOTS_DIR", str(tmp_path / "slots"))
    monkeypatch.delenv("DEV_TEST_REMOTE_LEASE", raising=False)
    monkeypatch.delenv("DEV_TEST_LEASE", raising=False)
    clock = [0.0]
    notices = []

    def sleep(_seconds):
        clock[0] += 1
        if clock[0] == 3:
            raise RuntimeError("Finished simulated wait")

    monkeypatch.setattr(module, "time", SimpleNamespace(time=lambda: 100, time_ns=lambda: 100, monotonic=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(module, "budget", lambda: 0)
    with pytest.raises(RuntimeError, match="Finished simulated wait"), module.lease(1, say=notices.append):
        pass
    assert notices and notices[0].startswith("testslots: queued for 1-1 workers: 0/0 permits in use, 0 run(s) ahead")


def test_the_slot_script_runs_from_the_checkout_whatever_copy_of_the_package_is_installed(tmp_path):
    shadow = tmp_path / "installed" / "poolhouse"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("")
    environment = {**os.environ, "PYTHONPATH": str(shadow.parent), "DEV_TEST_SLOTS_DIR": str(tmp_path / "slots")}
    done = subprocess.run([sys.executable, str(SCRIPT), "status"], env=environment, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert any(line.startswith("budget ") for line in done.stdout.splitlines())

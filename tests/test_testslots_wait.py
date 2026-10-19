"""A queued lease reads every slot record when it arrives and when no admission pass has run for a moment, not at every poll."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "testslots.py"

HOLDER = """
import importlib.util, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("testslots", sys.argv[1]); m = importlib.util.module_from_spec(spec)
sys.modules["testslots"] = m; spec.loader.exec_module(m)
with m.lease(1, 1, label="holder", say=lambda s: None):
    Path(sys.argv[2]).write_text("held")
    while not Path(sys.argv[3]).exists():
        time.sleep(0.01)
"""

WAITER = """
import importlib.util, json, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("testslots", sys.argv[1]); m = importlib.util.module_from_spec(spec)
sys.modules["testslots"] = m; spec.loader.exec_module(m)
decisions = []
real = m._attempt
m._attempt = lambda *args, **kwargs: decisions.append(time.time()) or real(*args, **kwargs)
with m.lease(1, 1, label="waiter", say=lambda s: None):
    granted = time.time()
Path(sys.argv[2]).write_text(json.dumps({"decisions": decisions, "granted": granted}))
"""


def child(program: str, *args: str, env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", program, str(SCRIPT), *args], env=env)


def until(path: Path, seconds: float = 30) -> None:
    end = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < end, f"{path.name} never appeared"
        time.sleep(0.02)


def test_the_head_of_the_queue_is_granted_as_soon_as_the_holder_leaves_and_runs_few_passes_before(tmp_path):
    inherited = {k: v for k, v in os.environ.items() if k not in {"DEV_TEST_REMOTE_LEASE", "DEV_TEST_LEASE"}}
    env = {**inherited, "DEV_TEST_SLOTS_DIR": str(tmp_path / "slots"), "DEV_TEST_BUDGET": "1", "DEV_TEST_WAIT_S": "60"}
    held, leave, report = tmp_path / "held", tmp_path / "leave", tmp_path / "report.json"
    holder = child(HOLDER, str(held), str(leave), env=env)
    until(held)
    waiter = child(WAITER, str(report), env=env)
    time.sleep(2.0)
    left = time.time()
    leave.write_text("go")
    until(report)
    holder.wait(10)
    waiter.wait(10)
    seen = json.loads(report.read_text())
    before = [t for t in seen["decisions"] if t < left]
    assert len(before) <= 8, f"{len(before)} admission passes in about 2 s with nothing changing"
    assert seen["granted"] - left < 0.5

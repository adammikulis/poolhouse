"""A pytest run that SIGTERM ends says which test was running."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_a_terminated_run_names_the_test_it_was_in(tmp_path):
    (tmp_path / "test_slow.py").write_text(
        "import time, pathlib\ndef test_waits():\n    pathlib.Path('in').write_text('1')\n    time.sleep(60)\n")
    run = subprocess.Popen([sys.executable, "-m", "pytest", "-q", "-p", "testinterrupt", "-p", "no:cacheprovider", "test_slow.py"],
                           cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(SCRIPTS)}, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, start_new_session=True)
    deadline = time.monotonic() + 60
    while not (tmp_path / "in").exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    run.send_signal(signal.SIGTERM)
    out, _ = run.communicate(timeout=60)
    assert "test: interrupted during test_slow.py::test_waits" in out, out

"""A script of the test runner run on its own, without the package installed, still finds `poolhouse` in its checkout."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"


@pytest.mark.parametrize("module", ["srcpath", "testhistory", "testqueue", "testslots", "testdurations"])
def test_a_runner_script_imports_poolhouse_from_its_own_checkout(module):
    code = f"import sys; sys.path.insert(0, {str(SCRIPTS)!r}); import {module}, poolhouse; print(poolhouse.__file__)"
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()).is_relative_to(ROOT / "src")

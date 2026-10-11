"""The helpers of the workspace load harness."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/experiments/workspace_load.py"
spec = importlib.util.spec_from_file_location("workspace_load", SCRIPT)
load = importlib.util.module_from_spec(spec)
sys.modules["workspace_load"] = load
spec.loader.exec_module(load)


def test_the_percentile_helpers():
    assert load.pct([], 0.5) == 0.0 and load.pct([3, 1, 2], 0.5) == 2 and load.pct(list(range(100)), 0.99) == 99
    assert load.summary([1.0, 2.0])["max"] == 2.0

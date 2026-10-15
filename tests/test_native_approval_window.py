"""Compile the inactive native window and exercise its pure request lifecycle."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "native-approval"


@pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS frameworks")
def test_native_approval_window_protocol_and_literal_build(tmp_path):
    env = os.environ.copy()
    env.update(HOME=str(tmp_path), TMPDIR=str(tmp_path))
    sources = sorted(SOURCE.glob("*.swift"))
    common = ["/usr/bin/swiftc", "-j", "1", "-parse-as-library", "-module-name", "PoolhouseApproval"]
    literal = tmp_path / "approval"
    subprocess.run([*common, *map(str, sources), "-o", str(literal)], check=True, env=env, timeout=120)
    pure = tmp_path / "protocol"
    subprocess.run([*common, "-D", "TESTING", *map(str, sources), "-o", str(pure)],
                   check=True, env=env, timeout=120)
    result = subprocess.run([str(pure)], check=True, capture_output=True, text=True, env=env, timeout=10)
    assert "pure protocol and lifecycle checks passed" in result.stdout
    # The shipped binary is compiled but never launched by a test.
    assert literal.stat().st_size > 0

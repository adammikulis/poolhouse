"""The inactive native authority binds each transition to its protected pending request."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testnode

ROOT = Path(__file__).resolve().parents[1]
RUSTUP_HOME = os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup"))
CARGO_HOME = os.environ.get("CARGO_HOME", str(Path.home() / ".cargo"))


def test_native_authority_rejects_replay_substitution_and_unauthorized_transitions():
    build = testnode.shared_build(ROOT / "app")
    staged = build.staged
    environment = {**os.environ, "RUSTUP_HOME": RUSTUP_HOME, "CARGO_HOME": CARGO_HOME,
                   "CARGO_TARGET_DIR": str(build.target), "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1"}
    result = subprocess.run([testnode.tool("cargo"), "test", "--offline", "--locked", "-p", "poolhouse-authority", "--lib"],
                            cwd=staged, env=environment, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = 45 if sys.platform == "darwin" else 17
    assert f"{expected} passed" in result.stdout and "0 failed" in result.stdout

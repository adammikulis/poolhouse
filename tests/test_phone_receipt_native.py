"""The native authority receipt protocol compiles and runs on isolated test keys."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testnode

ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.timeout(600)
def test_phone_receipt_codec(tmp_path):
    build = testnode.shared_build(ROOT / "app")
    environment = {**os.environ, "HOME": str(tmp_path), "TMPDIR": str(tmp_path),
                   "RUSTUP_HOME": str(testnode.account() / ".rustup"), "CARGO_HOME": str(testnode.account() / ".cargo"),
                   "CARGO_TARGET_DIR": str(build.target), "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1"}
    result = subprocess.run([testnode.tool("cargo"), "test", "--offline", "--locked", "-p", "poolhouse-authority",
                             "--test", "phone_receipt", "--", "--nocapture"], cwd=build.staged, env=environment,
                            capture_output=True, text=True, timeout=600)
    log = tmp_path / "phone-receipt.log"
    log.write_text(result.stdout + result.stderr)
    assert result.returncode == 0, f"Receipt tests failed; full output: {log}\n{result.stdout}"

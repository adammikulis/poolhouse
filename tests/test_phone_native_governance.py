"""The phone verifier retains checked governance evidence in private scratch state."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testnode

ROOT = Path(__file__).resolve().parents[1]
RUSTUP_HOME = os.environ.get("RUSTUP_HOME", str(Path.home() / ".rustup"))
CARGO_HOME = os.environ.get("CARGO_HOME", str(Path.home() / ".cargo"))


@pytest.mark.timeout(600)
def test_shared_native_governance_rejects_bad_checkpoints_and_retains_late_evidence():
    build = testnode.shared_build(ROOT / "app")
    staged = build.staged
    environment = {**os.environ, "RUSTUP_HOME": RUSTUP_HOME, "CARGO_HOME": CARGO_HOME, "CARGO_TARGET_DIR": str(build.target),
                   "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1"}
    result = subprocess.run([testnode.tool("cargo"), "test", "--offline", "--locked", "-p", "poolhouse-node",
                             "--lib", "gov::phone_verify", "--", "--nocapture"],
                            cwd=staged, env=environment, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "14 passed" in result.stdout and "0 failed" in result.stdout
    attestation = subprocess.run([testnode.tool("cargo"), "test", "--offline", "--locked", "-p", "poolhouse-node",
                                 "--lib", "phone::attest", "--", "--nocapture"],
                                cwd=staged, env=environment, capture_output=True, text=True, timeout=600)
    assert attestation.returncode == 0, attestation.stdout + attestation.stderr
    assert "10 passed" in attestation.stdout and "0 failed" in attestation.stdout
    boundary = subprocess.run([testnode.tool("cargo"), "test", "--offline", "--locked", "-p", "poolhouse-phone-governance", "--lib"],
                              cwd=staged, env=environment, capture_output=True, text=True, timeout=600)
    assert boundary.returncode == 0, boundary.stdout + boundary.stderr
    assert "2 passed" in boundary.stdout and "0 failed" in boundary.stdout
    if sys.platform != "win32":
        kernel = subprocess.run([testnode.tool("cargo"), "test", "--offline", "--locked", "-p", "poolhouse-node", "--lib", "sys::unix::tests"],
                              cwd=staged, env=environment, capture_output=True, text=True, timeout=600)
        assert kernel.returncode == 0, kernel.stdout + kernel.stderr
        assert "1 passed" in kernel.stdout and "0 failed" in kernel.stdout

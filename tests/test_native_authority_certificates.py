"""Detached native certificates prove only an expected issuer signature."""

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


def test_native_certificates_bind_canonical_descriptor_and_strict_issuer_signature():
    build = testnode.shared_build(ROOT / "app")
    staged = build.staged
    environment = {**os.environ, "RUSTUP_HOME": RUSTUP_HOME, "CARGO_HOME": CARGO_HOME,
                   "CARGO_TARGET_DIR": str(build.target), "CARGO_BUILD_JOBS": "1", "RUST_TEST_THREADS": "1"}
    result = subprocess.run([testnode.tool("cargo"), "test", "--offline", "--locked", "-p", "poolhouse-authority", "--lib", "certificate::"],
                            cwd=staged, env=environment, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    expected = 7
    assert f"{expected} passed" in result.stdout and "0 failed" in result.stdout

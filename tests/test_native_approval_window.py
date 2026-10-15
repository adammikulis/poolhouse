"""Compile the inactive native window and exercise its pure request lifecycle."""
from __future__ import annotations

import importlib.util
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
    spec = importlib.util.spec_from_file_location("native_approval_build", SOURCE / "build.py")
    assert spec and spec.loader
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    literal = tmp_path / "approval"
    build.compile_sources(literal, tmp_path, env=env)
    pure = tmp_path / "protocol"
    build.compile_sources(pure, tmp_path, testing=True, env=env)
    result = subprocess.run([str(pure)], check=False, capture_output=True, text=True, env=env, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "pure protocol and lifecycle checks passed" in result.stdout
    symbols = subprocess.run(["/usr/bin/nm", "-g", str(literal)], check=True, capture_output=True, text=True, env=env)
    assert "ph_manifest_fixture" not in symbols.stdout
    imports = subprocess.run(["/usr/bin/nm", "-u", str(literal)], check=True, capture_output=True, text=True, env=env)
    assert "xpc_connection_create" not in imports.stdout
    assert "xpc_connection_activate" not in imports.stdout
    assert "xpc_connection_resume" not in imports.stdout
    for symbol in ["_AuthorizationCreate", "_AuthorizationMakeExternalForm", "_AuthorizationCopyRights"]:
        assert symbol not in imports.stdout
    assert "LAContext" in imports.stdout
    assert "Fresh mock local presence" in result.stdout
    forged = tmp_path / "ForgedManifest.swift"
    forged.write_text("func forge(_ fields: ManifestFields) -> PublicManifest { PublicManifest(fields: fields) }", encoding="utf-8")
    rejection = subprocess.run(["/usr/bin/swiftc", "-j", "1", "-parse-as-library", "-typecheck",
                                "-import-objc-header", str(SOURCE / "NativeSender.h"),
                                *map(str, sorted(SOURCE.glob("*.swift"))), str(forged)],
                               capture_output=True, text=True, env=env, timeout=60)
    assert rejection.returncode != 0 and "private" in rejection.stderr
    forged.write_text("@MainActor func forgeResult(_ binding: PresenceBinding) -> LocalPresenceResult { LocalPresenceResult(binding: binding) }", encoding="utf-8")
    rejection = subprocess.run(["/usr/bin/swiftc", "-j", "1", "-parse-as-library", "-typecheck",
                                "-import-objc-header", str(SOURCE / "NativeSender.h"),
                                *map(str, sorted(SOURCE.glob("*.swift"))), str(forged)],
                               capture_output=True, text=True, env=env, timeout=60)
    assert rejection.returncode != 0 and "fileprivate" in rejection.stderr
    # The shipped binary is compiled but never launched by a test.
    assert literal.stat().st_size > 0

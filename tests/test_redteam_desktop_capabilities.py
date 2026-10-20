"""Desktop origins cannot invoke native shell or filesystem commands, and the window stays on its daemon."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from native_cargo import communicate, testnode

from poolhouse.platform import start_process

pytestmark = pytest.mark.redteam
ROOT = Path(__file__).resolve().parents[1]


def test_desktop_grants_only_window_and_close_commands():
    capability = json.loads((ROOT / "app/src-tauri/capabilities/main.json").read_text())
    assert capability["windows"] == ["main"]
    assert capability["remote"]["urls"] == ["http://127.0.0.1:8770/*"]
    assert capability["local"] is False
    assert set(capability["permissions"]) == {
        "core:default", "window-state:default", "allow-close-choice", "allow-on-closing", "allow-reopen-page",
        "allow-choose-studio-watch-source",
    }


@pytest.mark.slow
@pytest.mark.parametrize("selector", ["security_tests", "daemon::tests", "identity::tests"])
def test_native_capability_authority_rejects_hostile_origins_and_commands(tmp_path, selector):
    if sys.platform != "darwin":
        pytest.skip("native desktop capability enforcement runs on macOS")
    binary = Path(os.environ["DEV_TEST_APP_TESTS"])
    assert binary.is_file() and not binary.is_symlink()
    isolated = tmp_path / "home"
    isolated.mkdir()
    native_env = {**os.environ, "HOME": str(isolated), "TMPDIR": str(tmp_path),
                  "POOLHOUSE_HOME": str(tmp_path / "state")}
    child = start_process([str(binary), selector, "--nocapture", "--test-threads=1"],
                          cwd=tmp_path, env=native_env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = communicate(child, time.monotonic() + testnode.BUILD_TIMEOUT_S)
    assert child.returncode == 0, stdout + stderr
    assert "test result: ok" in stdout and " 0 failed" in stdout
    assert "running 0 tests" not in stdout

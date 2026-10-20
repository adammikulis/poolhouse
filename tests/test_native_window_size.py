"""Native geometry checks use the isolated maintained app test executable."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from native_cargo import communicate, testnode

from poolhouse.platform import start_process

ROOT = Path(__file__).resolve().parents[1]


def test_saved_geometry_restores_once_before_work_area_fit():
    source = (ROOT / 'app/src-tauri/src/main.rs').read_text()
    assert 'const WIDTH: f64 = 1600.0;' in source
    assert 'const HEIGHT: f64 = 1000.0;' in source
    assert '.skip_initial_state("main")' in source
    assert source.count('window.restore_state(StateFlags::all())?;') == 1
    assert source.index('window.restore_state(') < source.index('window_size::constrain(')
    geometry = (ROOT / 'app/src-tauri/src/window_size.rs').read_text()
    assert geometry.index('window.is_maximized()? || window.is_fullscreen()?') < geometry.index('window.set_size(')
    assert 'monitor.work_area()' in geometry


@pytest.mark.slow
def test_native_frame_geometry(tmp_path):
    if sys.platform != 'darwin':
        pytest.skip('native desktop tests run on macOS')
    binary = Path(os.environ['DEV_TEST_APP_TESTS'])
    isolated = tmp_path / 'home'
    isolated.mkdir()
    environment = {**os.environ, 'HOME': str(isolated), 'TMPDIR': str(tmp_path),
                   'POOLHOUSE_HOME': str(tmp_path / 'state')}
    child = start_process([str(binary), 'window_size::tests', '--test-threads=1'], cwd=tmp_path,
                          env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True)
    stdout, stderr = communicate(child, time.monotonic() + testnode.BUILD_TIMEOUT_S)
    assert child.returncode == 0, stdout + stderr
    assert 'test result: ok. 4 passed;' in stdout

"""Compile the actual app and exercise its isolated native picker and origin tests."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from native_cargo import ACCOUNT, communicate, testnode

from poolhouse.platform import start_process

ROOT = Path(__file__).resolve().parents[1]


def test_native_picker_and_app_capabilities(tmp_path):
    temporary = tmp_path / 'temporary'
    temporary.mkdir()
    isolated = tmp_path / 'home'
    isolated.mkdir()
    environment = {**os.environ, 'HOME': str(isolated), 'TMPDIR': str(temporary),
                   'RUSTUP_HOME': str(ACCOUNT / '.rustup'), 'CARGO_HOME': str(ACCOUNT / '.cargo'),
                   'POOLHOUSE_HOME': str(tmp_path / 'state')}
    binary = Path(os.environ['DEV_TEST_APP_TESTS'])
    assert binary.is_file() and not binary.is_symlink()
    argv = [str(binary), '--test-threads=1']
    process = start_process(argv, cwd=tmp_path, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stdout, stderr = communicate(process, time.monotonic() + testnode.BUILD_TIMEOUT_S)
    assert process.returncode == 0, stdout + stderr
    assert 'test result: ok.' in stdout and '0 failed;' in stdout, stdout + stderr
    assert 'studio_watch::tests::only_daemon_mode_and_actual_os_selection_reach_private_completion ... ok' in stdout
    assert 'security_tests::capabilities_reject_remote_shell_and_foreign_windows ... ok' in stdout

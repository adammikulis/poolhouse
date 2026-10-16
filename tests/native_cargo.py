"""Isolated native node unit checks with owned-process cleanup."""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from poolhouse.platform import start_process, terminate_process_group

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testnode

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = testnode.account()
CASES = {"attestation": ("phone::attest", 10), "socket": ("sys::unix::tests", 2)}


def communicate(process, deadline):
    """Collect an owned compiler's output within its fixed deadline."""
    try:
        return process.communicate(timeout=max(.001, deadline - time.monotonic() - 1))
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        if process.returncode is not None:
            error.add_note("Native compiler cleanup is unconfirmed: its leader was already reaped.")
            raise
        try:
            with contextlib.suppress(ProcessLookupError):
                terminate_process_group(process, force=True)
            stdout, stderr = process.communicate(timeout=max(.001, deadline - time.monotonic()))
        except (OSError, subprocess.TimeoutExpired) as cleanup:
            error.add_note("Native compiler cleanup failed.")
            raise error from cleanup
        if isinstance(error, subprocess.TimeoutExpired):
            error.stdout, error.stderr = stdout, stderr
        elif stdout or stderr:
            error.add_note("Native compiler output:\n" + stdout + stderr)
        raise


class NodeUnits:
    def __init__(self, work, temporary):
        self.work = work
        testnode.stage(ROOT / "app", testnode.sources(ROOT / "app"), work / "app")
        (work / "home").mkdir()
        self.environment = {**os.environ, "HOME": str(work / "home"), "TMPDIR": str(temporary),
                            "RUSTUP_HOME": str(ACCOUNT / ".rustup"), "CARGO_HOME": str(ACCOUNT / ".cargo"),
                            "CARGO_TARGET_DIR": str(work / "target"), "CARGO_BUILD_JOBS": "1"}

    def run(self, case):
        selected, count = CASES[case]
        argv = [testnode.tool("cargo"), "test", "--locked", "--offline", "-p", "poolhouse-node", "--lib",
                selected, "--", "--test-threads=1"]
        deadline = time.monotonic() + 600
        process = start_process(argv, cwd=self.work / "app", env=self.environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = communicate(process, deadline)
        assert process.returncode == 0, stdout + stderr
        expected = f"test result: ok. {count} passed; 0 failed;"
        assert any(line.startswith(expected) for line in stdout.splitlines()), stdout + stderr
        return stdout


@pytest.fixture(scope="session")
def native_node_units(tmp_path_factory):
    base = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="phnc-", dir=base) as directory:
        work = Path(directory).resolve()
        assert work.stat().st_mode & 0o777 == 0o700
        yield NodeUnits(tmp_path_factory.mktemp("native-node").resolve(), work)

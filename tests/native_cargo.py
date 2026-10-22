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
import testunits

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = testnode.account()
RUN_S = 600
"""Seconds one call may run its processes."""
CASES = {"attestation": ("phone::attest", 10), "socket": ("sys::unix::tests", 2), "replacement": ("log::replacement_tests", 2)}


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
    def __init__(self, work, temporary, app=ROOT / "app"):
        self.work = work
        self.built = {}
        build = testnode.shared_build(app)
        self.staged, self.target = build.staged, build.target
        (work / "home").mkdir()
        self.environment = {**os.environ, "HOME": str(work / "home"), "TMPDIR": str(temporary),
                            "RUSTUP_HOME": str(ACCOUNT / ".rustup"), "CARGO_HOME": str(ACCOUNT / ".cargo"),
                            "CARGO_TARGET_DIR": str(build.target), "CARGO_BUILD_JOBS": "1"}

    def run(self, case):
        """Run the library unit tests the case selects (built by ``prepare`` when not yet) and check how many passed."""
        selected, count = CASES[case]
        stdout = self.start_built(testunits.LIB, [selected])
        expected = f"test result: ok. {count} passed; 0 failed;"
        assert any(line.startswith(expected) for line in stdout.splitlines()), stdout
        return stdout

    def start_built(self, target, arguments=()):
        """Run the built executable of one target as cargo would (the package folder as the working folder); its output."""
        code, stdout, stderr = self.launch_built(target, arguments)
        assert code == 0, stdout + stderr
        return stdout

    def launch_built(self, target, arguments=()):
        self.prepare([target])
        package = self.staged / "poolhouse-node"
        environment = {**self.environment, "CARGO_MANIFEST_DIR": str(package)}
        return self.launch([str(self.built[target]), *arguments, "--test-threads=1"], package, environment, time.monotonic() + RUN_S)

    def prepare(self, targets):
        """Compile the targets not yet built, in one cargo call and with the build's own deadline; their executables are kept."""
        missing = [target for target in targets if target not in self.built]
        if missing:
            self.built.update(testunits.build(testnode.tool("cargo"), self.staged, missing, self.build_output, self.environment))

    def run_targets(self, targets):
        """Run the built executable of each target, one after another and every one even when an earlier failed; the output of all."""
        results = [self.launch_built(target) for target in targets]
        failed = [stdout + stderr for code, stdout, stderr in results if code]
        assert not failed, "\n".join(failed)
        stdout = "".join(result[1] for result in results)
        assert stdout.count("test result: ok.") == len(targets), stdout
        return stdout

    def build_output(self, argv, work, environment):
        code, stdout, stderr = self.launch(argv, work, environment, time.monotonic() + testnode.BUILD_TIMEOUT_S)
        assert code == 0, stderr[-4000:]
        return stdout

    def execute(self, argv):
        code, stdout, stderr = self.launch(argv, self.staged, self.environment, time.monotonic() + RUN_S)
        assert code == 0, stdout + stderr
        return stdout

    def launch(self, argv, cwd, environment, deadline):
        process = start_process(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = communicate(process, deadline)
        return process.returncode, stdout, stderr


@pytest.fixture(scope="session")
def native_node_units(tmp_path_factory):
    base = Path(tempfile.gettempdir()).resolve()
    with tempfile.TemporaryDirectory(prefix="phnc-", dir=base) as directory:
        work = Path(directory).resolve()
        assert work.stat().st_mode & 0o777 == 0o700
        yield NodeUnits(tmp_path_factory.mktemp("native-node").resolve(), work)


@pytest.fixture(scope="module")
def native_targets_built(native_node_units, request):
    """Compile once per module every Rust test target the module names in ``NATIVE_TARGETS`` (``testunits.LIB`` for the library's unit
    tests) or its selected tests are parametrized on (their ``target``); such a test carries ``@pytest.mark.timeout(N, func_only=True)``
    so N counts its run, not this build."""
    wanted = {*getattr(request.module, "NATIVE_TARGETS", ())}
    wanted.update(item.callspec.params["target"] for item in request.session.items
                  if item.module is request.module and "target" in getattr(getattr(item, "callspec", None), "params", {}))
    native_node_units.prepare(sorted(wanted))
    return native_node_units

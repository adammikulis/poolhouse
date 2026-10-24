"""Actual own-child mechanics, with no model, admission or confinement claim."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import runpy
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import testnode

ROOT = Path(__file__).resolve().parents[1]


def _logs(directory, arguments, pid, output):
    for name, data in output.items():
        path = directory / f"{Path(arguments[0]).name}-{pid}-{name}.log"
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(data)


def _execute(arguments, directory, environment):
    """Bounded own group; retain the unreaped leader until group cleanup."""
    import resource

    only_leader = runpy.run_path(str(ROOT / "patches/codex/macos/prepare_candidate.py"))["_only_unreaped_leader"]

    def no_core():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    process = subprocess.Popen(arguments, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                               close_fds=True, preexec_fn=no_core)
    ready = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    completed = None
    try:
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            ready.register(stream, selectors.EVENT_READ, name)
        # Same existing native-tool observation bounds as prepare_candidate._tool.
        deadline = time.monotonic() + 15
        while ready.get_map():
            if time.monotonic() > deadline:
                raise AssertionError("own-child fixture tool did not finish")
            for key, _ in ready.select(.05):
                block = os.read(key.fd, 4096)
                if not block:
                    ready.unregister(key.fileobj)
                else:
                    output[key.data].extend(block)
                    if len(output[key.data]) > 32768:
                        raise AssertionError("own-child fixture output exceeded bound")
        while completed is None and time.monotonic() < deadline:
            completed = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
            if completed is None:
                time.sleep(.01)
        assert completed is not None, "own-child fixture exit unavailable"
        assert completed.si_code == os.CLD_EXITED and completed.si_status == 0, output["stderr"].decode(errors="replace")
        return bytes(output["stdout"])
    finally:
        try:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                if completed is None or not only_leader(process.pid):
                    raise
        finally:
            try:
                process.wait(timeout=2)
            finally:
                ready.close()
                process.stdout.close()
                process.stderr.close()
                _logs(directory, arguments, process.pid, output)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin suspended-child mechanics")
def test_suspended_inert_fixture_is_cancelled_and_reaped(tmp_path):
    """One standalone supervisor is the sole waiter for one harmless child."""
    architecture = {"arm64": "aarch64", "x86_64": "x86_64"}[platform.machine()]
    rustc = testnode.account() / f".rustup/toolchains/stable-{architecture}-apple-darwin/bin/rustc"
    clang = Path("/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang")
    sdk = Path("/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk")
    assert rustc.is_file() and clang.is_file() and sdk.is_dir(), "installed native toolchain required"
    environment = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": str(tmp_path),
                   "TMPDIR": str(tmp_path), "POOLHOUSE_HOME": str(tmp_path / "state")}
    version = _execute([str(rustc), "--version", "--verbose"], tmp_path, environment)
    # Exact maintained Cargo.lock/compiler target key, with no staging or build.
    key = hashlib.sha256(b"\n".join([(ROOT / "app/Cargo.lock").read_bytes(), version])).hexdigest()[:testnode.KEY_LENGTH]
    dependencies = testnode.cargo_root() / "targets" / key / "debug/deps"
    candidates = list(dependencies.glob("liblibc-*.rlib"))
    assert len(candidates) == 1, "one exact maintained libc artifact required"
    image = tmp_path / "inert-child"
    _execute([str(clang), "-isysroot", str(sdk), "-fno-modules", "-fno-implicit-modules", "-std=c11",
              "-Wall", "-Wextra", "-Werror", str(ROOT / "tests/fixtures/native_inert_child.c"), "-o", str(image)], tmp_path, environment)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    module = ROOT / "app/poolhouse-node/src/lifecycle/macos_child.rs"
    wrapper = tmp_path / "supervisor.rs"
    wrapper.write_text(f'#![allow(dead_code)]\nextern crate libc;\n#[path={json.dumps(str(module))}] mod child;\n'
                       'fn main() { child::run_fixture(std::path::Path::new(&std::env::args_os().nth(1).unwrap())); }\n')
    supervisor = tmp_path / "supervisor"
    _execute([str(rustc), "--edition=2021", "--cfg", "test", str(wrapper), "--extern", f"libc={candidates[0]}",
              "-L", f"dependency={dependencies}", "-o", str(supervisor)], tmp_path, environment)
    result = _execute([str(supervisor), str(image)], tmp_path, environment)
    assert result.strip() == b"suspended fixture cancelled and exact child reaped"
    assert hashlib.sha256(image.read_bytes()).hexdigest() == digest

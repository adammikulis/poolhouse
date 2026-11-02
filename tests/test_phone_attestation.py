"""Attestation unit checks and native compiler cancellation ownership."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest
import testunits

from poolhouse.platform import start_process

pytest_plugins = ["native_cargo"]
NATIVE_TARGETS = (testunits.LIB,)


@pytest.fixture
def native_cargo_module(request):
    return request.config.pluginmanager.get_plugin("native_cargo")


@pytest.mark.slow
@pytest.mark.timeout(120, func_only=True)
def test_phone_attestation(native_targets_built):
    native_targets_built.run("attestation")


@pytest.mark.parametrize("interrupted,reaped", [(False, False), (True, False), (True, True)])
def test_compiler_cancellation_preserves_ownership(monkeypatch, native_cargo_module, interrupted, reaped):
    error = KeyboardInterrupt() if interrupted else subprocess.TimeoutExpired("cargo", 599)
    signals = []

    class Child:
        returncode = 0 if reaped else None
        calls = 0

        def communicate(self, *, timeout):
            assert 0 < timeout <= 600
            self.calls += 1
            if self.calls == 1:
                raise error
            self.returncode = -signal.SIGKILL
            return "compiler output", "compiler diagnostic"

    child = Child()
    monkeypatch.setattr(native_cargo_module, "terminate_process_group", lambda proc, **kw: signals.append((proc, kw)))
    with pytest.raises(type(error)) as caught:
        native_cargo_module.communicate(child, time.monotonic() + 600)
    assert caught.value is error
    assert signals == ([] if reaped else [(child, {"force": True})])
    assert child.calls == (1 if reaped else 2)
    if reaped:
        assert "unconfirmed" in error.__notes__[0]
    elif not interrupted:
        assert error.stdout == "compiler output" and error.stderr == "compiler diagnostic"


@pytest.mark.skipif(os.name != "posix", reason="Owned Unix process group")
def test_compiler_timeout_stops_its_owned_group(monkeypatch, native_cargo_module):
    child = start_process([sys.executable, "-c", "import time; time.sleep(60)"],
                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    clock = time.monotonic
    monkeypatch.setattr(native_cargo_module.time, "monotonic", lambda: clock() + 599)
    with pytest.raises(subprocess.TimeoutExpired):
        native_cargo_module.communicate(child, clock() + 600)
    assert child.returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.killpg(child.pid, 0)

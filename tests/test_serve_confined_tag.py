"""A confined model server's run stays registered with the sandbox backend for as long as the server lives."""

from __future__ import annotations

import time

import pytest

from poolhouse.serve import (
    LlamaServerBackend,
    ServerManager,
    ServerSpec,
    backend as backend_module,
    free_port,
)
from poolhouse.serve.confined import Confined
from poolhouse.testing.canned import canned_binary
from tests.sandbox_kit import RecordingBackend

pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")


def waited(condition, within=10.0):
    deadline = time.monotonic() + within
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()


def test_a_healthy_confined_server_keeps_its_run_until_it_is_stopped(tmp_path, monkeypatch):
    recorder = RecordingBackend()
    monkeypatch.setattr(backend_module.confinement, "confine", lambda argv, env, binary, **_: Confined(
        list(argv), dict(env), "served", recorder, time.time(), str(tmp_path)))
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF" + b"\x00" * 64)
    binary = canned_binary(tmp_path)
    manager = ServerManager(LlamaServerBackend(binary=binary, sandboxed=True), state_file=tmp_path / "leases.json")
    info = manager.lease(ServerSpec(model=model, port=free_port(), context=512), timeout=60,
                         preflight=False, warmup_request=False)
    try:
        assert not waited(lambda: recorder.finished, within=0.5)
    finally:
        manager.release(info)
    assert waited(lambda: recorder.finished) and recorder.finished == ["served"]

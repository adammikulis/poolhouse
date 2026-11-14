"""One memory ledger for the core and the pool: a server either started is counted by the other's admission.

The core's servers are the canned plumbing server (`poolhouse.testing.canned`), real processes on real ports; the pool's go
through a real `ServerManager` and the real node's lease table, with a backend that starts a real child process. What is
under test is admission, so memory is the one made-up quantity: the machine allows `TOTAL_MB` and each server takes `EACH_MB`.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from model_approval import approved
from test_core import current_build, model  # noqa: F401 - the fixtures of the core's own tests
from test_serve_admission import ServedBackend

from poolhouse import core, limits
from poolhouse.serve import admission, ledger
from poolhouse.serve.backend import ServerSpec
from poolhouse.serve.leases import lease_file, recorded_servers
from poolhouse.serve.manager import ServerManager
from poolhouse.serve.ports import free_port

pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")

MIB = 1 << 20
TOTAL_MB, EACH_MB = 1000, 600


@pytest.fixture(autouse=True)
def machine(monkeypatch):
    """A machine that allows `TOTAL_MB`, servers that each take `EACH_MB`, and a start that waits a second for memory."""
    limits.changed(memory_bytes=TOTAL_MB * MIB)
    monkeypatch.setattr(admission, "budget_mb", lambda: TOTAL_MB)
    monkeypatch.setattr(admission, "estimate_bytes", lambda spec: EACH_MB * MIB)
    monkeypatch.setenv(admission.ENV_WAIT, "1")


@pytest.fixture
def backend():
    made = ServedBackend()
    yield made
    made.close()


@pytest.fixture
def weights(tmp_path) -> Path:
    path = tmp_path / "pool-model.gguf"
    path.write_bytes(b"GGUF" + b"\x00" * 64)
    approved(path)
    return path


def pool(backend) -> ServerManager:
    """A manager over the machine's own server file, as the machine's broker has."""
    return ServerManager(backend, state_file=lease_file())


def asking(path: Path) -> ServerSpec:
    return ServerSpec(model=path, port=free_port(), context=4096)


def test_a_pool_start_is_refused_beside_a_core_server_that_holds_the_memory(current_build, model, weights, backend):  # noqa: F811
    with core.serve(model, 2048, preflight=False):
        with pytest.raises(admission.AdmissionRefused, match=r"plumbing-model\.gguf"):
            pool(backend).lease(asking(weights))
        assert backend.started == []


def test_a_core_start_is_refused_beside_a_pool_server_that_holds_the_memory(current_build, model, weights, backend):  # noqa: F811
    held = pool(backend)
    info = held.lease(asking(weights))
    try:
        with pytest.raises(core.AdmissionRefused, match=r"pool-model\.gguf"):
            core.serve(model, 2048, preflight=False)
    finally:
        held.release(info)


def test_a_pool_start_waits_in_the_ledger_and_not_only_in_the_node(current_build, model, weights, backend):  # noqa: F811
    """Between the moment the pool asks and the moment the node grants, the pool's memory is already a record the core counts."""
    held = pool(backend)
    info = held.lease(asking(weights))
    try:
        records = recorded_servers(lease_file())
        assert [one["est_mb"] for one in records.values()] == [EACH_MB]
        assert ledger.fits(records, EACH_MB)[0] is False
    finally:
        held.release(info)
    assert recorded_servers(lease_file()) == {}


def test_a_core_and_a_pool_asking_at_once_cannot_both_take_the_memory(current_build, model, weights, backend, monkeypatch):  # noqa: F811
    monkeypatch.setenv(admission.ENV_WAIT, "3")
    outcome: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def from_core():
        barrier.wait()
        try:
            outcome["core"] = core.serve(model, 2048, preflight=False)
        except core.AdmissionRefused as refused:
            outcome["core"] = refused

    def from_pool():
        barrier.wait()
        try:
            outcome["pool"] = pool(backend).lease(asking(weights))
        except admission.AdmissionRefused as refused:
            outcome["pool"] = refused

    threads = [threading.Thread(target=from_core), threading.Thread(target=from_pool)]
    for one in threads:
        one.start()
    for one in threads:
        one.join(timeout=60)
    refused = [name for name, got in outcome.items() if isinstance(got, admission.AdmissionRefused)]
    assert len(outcome) == 2 and len(refused) == 1, outcome
    live = recorded_servers(lease_file())
    assert len(live) == 1 and sum(admission.entry_mb(one) for one in live.values()) <= TOTAL_MB
    if isinstance(outcome.get("core"), core.Handle):
        outcome["core"].close()


def test_two_holders_in_one_process_keep_each_others_records(current_build, model, weights, backend, monkeypatch):  # noqa: F811
    monkeypatch.setattr(admission, "budget_mb", lambda: 4 * TOTAL_MB)
    held = pool(backend)
    info = held.lease(asking(weights))
    try:
        with core.serve(model, 2048, preflight=False) as server:
            assert sorted(recorded_servers(lease_file())) == sorted([info.port, server.port])
            held._save()   # the manager writes its records over the file: the core's stay
            assert server.port in recorded_servers(lease_file())
    finally:
        held.release(info)


def test_a_pool_server_gets_its_key_in_a_private_file_and_the_file_goes_with_the_server(current_build, model):  # noqa: F811
    import json

    from poolhouse import serverkeys
    from poolhouse.serve.backend import LlamaServerBackend

    held = ServerManager(LlamaServerBackend(), state_file=lease_file())
    info = held.lease(ServerSpec(model=model, port=free_port(), context=2048), preflight=False)
    keyfile = serverkeys.key_file(info.port)
    try:
        argv = json.loads((current_build.parent / "argv.json").read_text())
        assert "--api-key" not in argv and argv[argv.index("--api-key-file") + 1] == str(keyfile)
        assert keyfile.read_text().strip() == serverkeys.for_url(info.base_url) != ""
        assert keyfile.stat().st_mode & 0o777 == 0o600
    finally:
        held.release(info)
    assert not keyfile.exists() and serverkeys.for_url(info.base_url) == ""

"""No server is stopped without proof that nothing uses it (`poolhouse.serve.inuse`).

2026-11-27: a model server was stopped while it generated. Each server here is the canned plumbing server (`poolhouse.testing.canned`, marked
CANNED, never a model) as a real process on a free port, and each record is under the test's own folder.
"""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_serve_admission import weights
from test_serve_broker import llama_binary, models  # noqa: F401  (fixtures)
from test_serve_refused_key import (  # noqa: F401  (fixtures)
    asked,
    backend,
    earlier,
    held,
    machine,
    manager,
    processes,
    recorded,
    started,
)

from poolhouse import serverkeys
from poolhouse.files import read_json
from poolhouse.serve import LlamaServerBackend, ServerManager, exit_guard, refused_keys
from poolhouse.serve.broker import Ask, Broker, BrokerError
from poolhouse.serve.process import kill_process_tree, pid_exists

pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")


@pytest.fixture
def session(processes):  # noqa: F811
    """A live process that stands for somebody's session holding a server."""
    one = subprocess.Popen(["sleep", "120"])
    processes.append(one)
    return one


def served(processes, state: Path, model: Path, **fields):  # noqa: F811
    """A server this build keyed, recorded as left up and holding itself."""
    from poolhouse.serve.ports import free_port

    port = free_port()
    key = serverkeys.issue(port)
    up = started(processes, model, key, port)
    serverkeys.bind(port, up.process.pid)
    recorded(state, up, model, **fields)
    return up


def idle_broker(manager) -> Broker:  # noqa: F811
    """A broker that takes the recorded server in and counts it idle at once."""
    made = Broker(manager, idle_s=-1.0, scan=lambda: [])
    made.adopt()
    return made


def test_the_idle_upkeep_keeps_a_server_whose_slots_cannot_be_read(tmp_path, manager, processes):  # noqa: F811
    up = served(processes, manager.state_file, weights(tmp_path, "qwen.gguf", 1))
    broker = idle_broker(manager)
    broker.servers[up.port].holders.clear()
    broker.busy = lambda url: None
    assert broker.reap() == []
    assert up.process.poll() is None


def test_the_idle_upkeep_keeps_a_server_only_the_file_of_holders_says_is_held(tmp_path, manager, processes, session):  # noqa: F811
    up = served(processes, manager.state_file, weights(tmp_path, "qwen.gguf", 1))
    broker = idle_broker(manager)
    broker.servers[up.port].holders.clear()
    held(manager.state_file, up, {"lease-another-broker-wrote": [session.pid, "a delegate"]})
    broker.busy = lambda url: False
    assert broker.reap() == []
    assert up.process.poll() is None


def test_a_second_broker_over_the_same_records_keeps_the_first_ones_holders(tmp_path, manager, processes, session):  # noqa: F811
    up = served(processes, manager.state_file, weights(tmp_path, "qwen.gguf", 1))
    held(manager.state_file, up, {"lease-of-the-machine-broker": [session.pid, "a delegate"]})
    other = Broker(manager, scan=lambda: [])   # as a build's smoke test starts one over the machine's records
    with other._cond:
        other._write_held()
    assert "lease-of-the-machine-broker" in read_json(manager.state_file.with_name("broker-leases.json"), {})[str(up.port)]["holders"]


def test_an_orphaned_server_a_live_process_holds_survives_the_sweep(tmp_path, manager, processes, session):  # noqa: F811
    gone = subprocess.Popen(["sleep", "0"])
    gone.wait()
    up = served(processes, manager.state_file, weights(tmp_path, "qwen.gguf", 1), owner_pid=gone.pid)
    held(manager.state_file, up, {"lease-carried-over": [session.pid, "a delegate"]})
    said: list[str] = []
    manager._sweep_orphans(0, said.append)
    assert up.process.poll() is None and any("left running" in line and "lease-carried-over" in line for line in said), said


def test_a_refused_key_stops_nothing_while_the_holders_cannot_be_read(tmp_path, manager, processes):  # noqa: F811
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, manager.state_file, model)
    manager.state_file.with_name("broker-leases.json").write_text("{ half a wri", encoding="utf-8")
    broker = Broker(manager, scan=lambda: [])
    with pytest.raises(refused_keys.ServerFailed, match="cannot be read"):
        refused_keys.settle(broker, asked(model), lambda _line: None)
    assert old.process.poll() is None


def test_a_broker_s_exit_leaves_a_server_another_process_holds(tmp_path, llama_binary, models, session):  # noqa: F811
    own = ServerManager(LlamaServerBackend(binary=llama_binary), state_file=tmp_path / "servers.json")
    broker = Broker(own, room=lambda: None, scan=lambda: [])
    grant = broker.lease(Ask(purpose="chat", models=(models[0],), pid=session.pid, spec={"context": 512}), timeout=30)
    pid = broker.servers[grant.port].pid
    try:
        assert pid not in exit_guard.guarded()
    finally:
        kill_process_tree(pid)


@pytest.fixture
def full(tmp_path, llama_binary, models, session):  # noqa: F811
    """A broker whose budget holds one model, with one server up that its holder let go: another model fits only in its place."""
    own = ServerManager(LlamaServerBackend(binary=llama_binary), state_file=tmp_path / "servers.json")
    broker = Broker(own, room=lambda: 0, scan=lambda: [])
    first = broker.lease(Ask(purpose="chat", models=(models[0],), pid=session.pid, weight=1 << 30, spec={"context": 512}), timeout=30)
    broker.release(first.lease)
    yield SimpleNamespace(broker=broker, port=first.port, pid=broker.servers[first.port].pid, other=models[1])
    for one in list(broker.servers.values()):
        kill_process_tree(one.pid)


@pytest.mark.parametrize("purpose", ["chat", "other"], ids=["same-purpose", "for-room"])
def test_a_server_in_use_is_not_evicted_and_nothing_is_loaded_beside_it_until_it_is_free(full, monkeypatch, purpose):
    monkeypatch.setattr("poolhouse.serve.broker_stops.SPARE_S", 0.3, raising=False)
    wanted = Ask(purpose=purpose, models=(full.other,), pid=os.getpid(), weight=1 << 30, spec={"context": 512})
    with socket.create_connection(("127.0.0.1", full.port), timeout=5):   # a request somebody without a lease has on it
        with pytest.raises(BrokerError, match="a request is in progress"):
            full.broker.lease(wanted, timeout=3)
        assert pid_exists(full.pid) and [(one.port, one.pid) for one in full.broker.servers.values()] == [(full.port, full.pid)]
    second = full.broker.lease(wanted, timeout=30)
    assert not pid_exists(full.pid) and [one.port for one in full.broker.servers.values()] == [second.port]


def test_a_server_another_install_keyed_is_listed_and_never_asked(tmp_path, manager, processes, monkeypatch):  # noqa: F811
    model = weights(tmp_path, "qwen.gguf", 1)
    up = started(processes, model, "the-installed-key")
    row = {"pid": up.process.pid, "port": up.port, "model": str(model), "defunct": False, "rss": 0,
           "key_file": str(tmp_path / "installed" / "server-keys.d" / "1.key")}
    asked_of_it: list[str] = []
    for name in ("is_healthy", "reported_models", "serving_params"):
        monkeypatch.setattr(f"poolhouse.serve.broker.{name}", lambda url, *a, _name=name, **k: asked_of_it.append(_name))
    broker = Broker(manager, scan=lambda: [row])
    broker.adopt()
    assert asked_of_it == []
    assert broker.servers[up.port].unmanaged and not broker.servers[up.port].ours

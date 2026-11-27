"""A server an earlier build left up refuses this build's key: it is never shared, it is replaced when nothing live holds it, else the launch
is refused naming the lease.

The earlier build's server is the canned plumbing server (`poolhouse.testing.canned`, marked CANNED, never a model) as a real process on a
real port, asking for a key this build holds nowhere it reads. Its record and its old key files are the ones an earlier build wrote.
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_serve_admission import GIB, ServedBackend, weights

from poolhouse import chat, do, harnessing, home, limits, serverkeys
from poolhouse.client import serving_params
from poolhouse.files import write_json
from poolhouse.serve import admission, refused_keys
from poolhouse.serve.backend import ServerFailed, ServerSpec
from poolhouse.serve.broker import Ask, Broker
from poolhouse.serve.leases import lease_file, recorded_servers
from poolhouse.serve.manager import ServerManager
from poolhouse.serve.ports import free_port
from poolhouse.serve.process import cmdline_digest, started_at
from poolhouse.testing.registry import record_server

pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")

SRC = str(Path(__file__).resolve().parent.parent / "src")
KEYED_STAND_IN = """
import sys, threading
from poolhouse.testing.canned import CANNED_MARKER, Canned, CannedServer
server = CannedServer(Canned(served_path=sys.argv[2], context=4096, slots=1), port=int(sys.argv[1]))
server.api_key = sys.argv[3]
print(CANNED_MARKER, "plumbing server up, nothing here is a model", flush=True)
threading.Event().wait()
"""


@dataclass
class Up:
    process: subprocess.Popen
    port: int


@pytest.fixture
def machine(monkeypatch):
    limits.changed(memory_bytes=10 * GIB)
    monkeypatch.setenv(admission.ENV_WAIT, "1")


@pytest.fixture
def processes():
    made: list[subprocess.Popen] = []
    yield made
    for one in made:
        one.kill()
        one.wait()


@pytest.fixture
def backend():
    made = ServedBackend()
    yield made
    made.close()


@pytest.fixture
def manager(tmp_path, backend, machine):
    return ServerManager(backend, state_file=tmp_path / "servers.json")


def started(processes, model: Path, key: str, port: int = 0) -> Up:
    port = port or free_port()
    process = subprocess.Popen([sys.executable, "-c", KEYED_STAND_IN, str(port), model.name, key], stdout=subprocess.PIPE, text=True,
                               env={**os.environ, "PYTHONPATH": SRC})
    processes.append(process)
    assert "CANNED" in process.stdout.readline()
    return Up(process, port)


def recorded(state: Path, up: Up, model: Path, **fields) -> None:
    """The record of a server left up in the background: it holds itself, as `serve(keep=True)` leaves it."""
    pid = up.process.pid
    record_server(state, up.port, model=str(model), pid=pid, **{
        "owner_pid": pid, "context": 4096, "parallel": 1, "started": started_at(pid), "cmdline": cmdline_digest(pid), **fields})


def held(state: Path, up: Up, holders: dict) -> None:
    write_json(state.with_name("broker-leases.json"), {str(up.port): {"purpose": "", "model": "", "holders": holders, "why": {}}})


def earlier(processes, state: Path, model: Path, **fields) -> Up:
    """A server an earlier build left up, with the key kept the way that build kept it: under the port's own name."""
    up = started(processes, model, "the-key-an-earlier-build-gave-it")
    folder = home.state("server-keys.d")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{up.port}.key").write_text("the-key-an-earlier-build-gave-it\n", encoding="utf-8")
    write_json(home.state("server-keys.json"), {str(up.port): {"pid": up.process.pid}})
    recorded(state, up, model, **fields)
    held(state, up, {f"up-{up.port}": [up.process.pid, "poolhouse-serve up"]})
    return up


def asked(model: Path, port: int = 0) -> ServerSpec:
    return ServerSpec(model=model, port=port or free_port(), context=4096)


@pytest.mark.parametrize("on_its_port", [True, False])
def test_a_refused_server_nothing_live_holds_is_replaced_and_said_in_one_line(tmp_path, backend, manager, processes, on_its_port):
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, manager.state_file, model)
    told: list[str] = []
    info = manager.lease(asked(model, old.port if on_its_port else 0), say=told.append)
    assert old.process.wait(timeout=10) is not None
    assert not info.adopted and len(backend.started) == 1
    assert serving_params(info.base_url) is not None
    lines = [one for one in told if f"port {old.port}:" in one and "key" in one]
    assert len(lines) == 1 and "replaced" in lines[0], told
    assert recorded_servers(manager.state_file).get(old.port, {}).get("pid") != old.process.pid


def test_the_server_whose_key_this_build_holds_is_shared_while_the_refused_one_goes(tmp_path, backend, manager, processes):
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, manager.state_file, model)
    port = free_port()
    key = serverkeys.issue(port)
    current = started(processes, model, key, port)
    serverkeys.bind(port, current.process.pid)
    recorded(manager.state_file, current, model)
    info = manager.lease(asked(model))
    assert (info.port, info.adopted) == (current.port, True) and not backend.started
    assert old.process.wait(timeout=10) is not None


@pytest.mark.parametrize("holder", ["a broker lease", "the recorded owner"])
def test_a_refused_server_something_live_holds_fails_the_launch_naming_the_lease(tmp_path, backend, manager, processes, holder):
    model = weights(tmp_path, "qwen.gguf", 1)
    session = subprocess.Popen(["sleep", "120"])
    processes.append(session)
    if holder == "a broker lease":
        old = earlier(processes, manager.state_file, model)
        held(manager.state_file, old, {"lease-of-a-live-session": [session.pid, "a claude session"]})
    else:
        old = earlier(processes, manager.state_file, model, owner_pid=session.pid, lease="lease-of-a-live-session")
        held(manager.state_file, old, {})
    with pytest.raises(ServerFailed) as refused:
        manager.lease(asked(model))
    said = str(refused.value)
    assert "lease-of-a-live-session" in said and f"pid {session.pid}" in said and "key" in said and str(old.port) in said
    assert old.process.poll() is None and not backend.started


def test_a_refused_server_with_a_request_on_it_is_left_running_and_the_launch_fails(tmp_path, backend, manager, processes):
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, manager.state_file, model)
    with socket.create_connection(("127.0.0.1", old.port), timeout=5):
        with pytest.raises(ServerFailed, match="a request is in progress"):
            manager.lease(asked(model))
        assert old.process.poll() is None and not backend.started


@pytest.mark.parametrize("record", [{"unmanaged": True}, {"started": None, "cmdline": None}], ids=["unmanaged", "unproven"])
def test_a_refused_server_the_broker_does_not_own_is_neither_stopped_nor_shared(tmp_path, backend, manager, processes, record):
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, manager.state_file, model, **record)
    info = manager.lease(asked(model, old.port))
    assert info.port != old.port and not info.adopted and len(backend.started) == 1
    assert old.process.poll() is None


@pytest.mark.parametrize("starter_gone", [False, True], ids=["starter-itself", "starter-gone"])
def test_another_models_refused_server_on_the_asked_port_is_served_beside_never_adopted(tmp_path, backend, manager, processes, starter_gone):
    other, model = weights(tmp_path, "other.gguf", 1), weights(tmp_path, "qwen.gguf", 1)
    session, gone = subprocess.Popen(["sleep", "120"]), subprocess.Popen(["sleep", "0"])
    processes.extend([session, gone])
    gone.wait()
    old = earlier(processes, manager.state_file, other, **({"owner_pid": gone.pid} if starter_gone else {}))
    held(manager.state_file, old, {"lease-of-a-live-session": [session.pid, "a claude session"]})
    info = manager.lease(asked(model, old.port))
    assert info.port != old.port and not info.adopted and old.process.poll() is None


def test_a_broker_lease_replaces_the_refused_server_and_never_shares_it(tmp_path, backend, manager, processes):
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, manager.state_file, model)
    broker = Broker(manager, scan=lambda: [])   # the machine's own servers are none of this test's
    grant = broker.lease(Ask(purpose="chat", models=(str(model),), pid=os.getpid(), spec={"context": 4096}), timeout=10)
    assert not grant.shared and len(backend.started) == 1
    assert old.process.wait(timeout=10) is not None


def test_two_askers_at_once_replace_a_refused_server_once(tmp_path, manager, processes, monkeypatch):
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, manager.state_file, model)
    both, look = threading.Barrier(2), refused_keys.request_on

    def together(pid, port):
        with contextlib.suppress(threading.BrokenBarrierError):   # the second asker cannot get this far while the first is here
            both.wait(timeout=2)
        return look(pid, port)

    monkeypatch.setattr(refused_keys, "request_on", together)
    broker, told = Broker(manager, scan=lambda: []), []
    askers = [threading.Thread(target=refused_keys.settle, args=(broker, asked(model), told.append)) for _ in range(2)]
    for one in askers:
        one.start()
    for one in askers:
        one.join(timeout=30)
    assert old.process.wait(timeout=10) is not None
    assert len([one for one in told if "replaced" in one]) == 1, told


def test_a_session_does_not_call_a_refused_server_loading_or_up(tmp_path, processes):
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, lease_file(), model)
    said: list[str] = []
    assert harnessing.adopted(str(model), harnessing.Want(port=old.port), None, said.append) is None
    assert said == []


def test_chat_leases_a_server_rather_than_connect_to_a_refused_one(tmp_path, processes, monkeypatch, capsys):
    model = weights(tmp_path, "qwen.gguf", 1)
    old = earlier(processes, lease_file(), model)
    monkeypatch.setattr("poolhouse.hub.located", lambda *a, **k: model)
    monkeypatch.setattr("poolhouse.serve.serving.drafted", lambda config, *a, **k: config)
    monkeypatch.setattr("poolhouse.serve.serving.slot", lambda config, **k: f"a slot leased on {config.port}")   # the broker's half
    args = chat.COMMAND.parser().parse_args(["look", "--model", str(model), "--port", str(old.port)])
    assert do.client_for(args) == f"a slot leased on {old.port}"
    assert "already up" not in capsys.readouterr().out

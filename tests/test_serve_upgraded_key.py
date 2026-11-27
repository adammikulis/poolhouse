"""A server the previous build keyed, after an update: its key record has no start time. It keeps working with no command when this computer can
show now that the process is the one the key was bound to, and is sent nothing, with the reason, when it cannot.

The server is the canned plumbing server (marked CANNED, never a model) as a real process on a real port, with its key and lease record written
as the previous build wrote them.
"""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest
from test_serve_admission import ServedBackend, weights
from test_serve_refused_key import asked, held, machine, recorded, started

from poolhouse import claude, harnessing, home, serverkeys
from poolhouse.client import serving_params
from poolhouse.files import read_json, write_json
from poolhouse.serve.backend import ServerFailed
from poolhouse.serve.manager import ServerManager
from poolhouse.serve.ports import free_port

__all__ = ["machine"]

pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")


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
    return ServerManager(backend, state_file=home.moved("servers.json"))


def index():
    return home.state("server-keys.json")


def previous(processes, state, model, **fields):
    """A server the previous build started, still up: its key issued before it, bound after it with no start time, and its lease record."""
    port = free_port()
    key = serverkeys.issue(port)
    up = started(processes, model, key, port)
    serverkeys.bind(port, up.process.pid)
    entries = read_json(index(), {})
    del entries[str(port)]["started"]
    write_json(index(), entries)
    recorded(state, up, model, **fields)
    held(state, up, {f"up-{port}": [up.process.pid, "poolhouse-serve up"]})
    return up, key


def entry(port):
    return read_json(index(), {})[str(port)]


def test_a_request_to_a_server_the_previous_build_keyed_carries_its_key_and_its_start_time_is_recorded(tmp_path, manager, processes):
    up, key = previous(processes, manager.state_file, weights(tmp_path, "qwen.gguf", 1))
    assert "started" not in entry(up.port)
    assert serverkeys.for_url(f"http://127.0.0.1:{up.port}") == key
    assert abs(entry(up.port)["started"] - __import__("psutil").Process(up.process.pid).create_time()) < 1
    assert serving_params(f"http://127.0.0.1:{up.port}") is not None


@pytest.mark.parametrize("held_by", ["itself", "a live session"])
def test_a_lease_after_an_update_takes_the_previous_builds_server_as_it_stands(tmp_path, backend, manager, processes, held_by):
    model = weights(tmp_path, "qwen.gguf", 1)
    session = subprocess.Popen(["sleep", "120"])
    processes.append(session)
    up, _key = previous(processes, manager.state_file, model)
    if held_by == "a live session":
        held(manager.state_file, up, {"lease-of-a-live-session": [session.pid, "a claude session"]})
    told: list[str] = []
    info = manager.lease(asked(model), say=told.append)
    assert (info.port, info.adopted) == (up.port, True) and not backend.started and up.process.poll() is None, told
    assert not [line for line in told if "refuses" in line], told


def test_on_after_an_update_uses_the_previous_builds_server(tmp_path, manager, processes):
    up, _key = previous(processes, manager.state_file, weights(tmp_path, "qwen.gguf", 1))
    said, ran = [], []
    status = harnessing.serve_and_run(SimpleNamespace(on=f"http://localhost:{up.port}"), "opencode", said.append, claude.alias_of,
                                      lambda served: ran.append(served) or 0)
    assert status == 0 and ran[0][0] == f"http://127.0.0.1:{up.port}" and ran[0][1], said


def _unprovable(case, up, processes, state, model):
    entries = read_json(index(), {})
    one = entries[str(up.port)]
    if case == "started-before-its-key-was-issued":
        one["at"] += 600
        write_json(index(), entries)
    elif case == "started-after-the-key-was-bound":
        long_ago = one["at"] - 600
        os.utime(index(), (long_ago, long_ago))
        one["at"] = long_ago - 600
    elif case == "another-command-line":
        recorded(state, up, model, cmdline="0" * 64)
    elif case == "no-record":
        state.write_text("{}", encoding="utf-8")
    else:   # the key and the record name a live process that is not the one listening
        processes.append(subprocess.Popen(["sleep", "120"]))
        other = processes[-1].pid
        one["pid"] = other
        write_json(index(), entries)
        recorded(state, SimpleNamespace(process=processes[-1], port=up.port), model)


@pytest.mark.redteam
@pytest.mark.parametrize(("case", "why"), [
    ("started-before-its-key-was-issued", "did not start between"), ("started-after-the-key-was-bound", "did not start between"),
    ("another-command-line", "does not match the process"), ("no-record", "no lease record"), ("another-process", "does not listen")])
def test_a_key_of_the_previous_build_that_cannot_be_proved_is_sent_nowhere_and_says_why(tmp_path, manager, processes, case, why):
    model = weights(tmp_path, "qwen.gguf", 1)
    up, _key = previous(processes, manager.state_file, model)
    _unprovable(case, up, processes, manager.state_file, model)
    assert serverkeys.for_url(f"http://127.0.0.1:{up.port}") == ""
    assert why in serverkeys.unproved(up.port), serverkeys.unproved(up.port)
    assert "started" not in entry(up.port)


def test_a_held_server_whose_old_key_cannot_be_proved_fails_the_launch_with_why(tmp_path, backend, manager, processes):
    model = weights(tmp_path, "qwen.gguf", 1)
    session = subprocess.Popen(["sleep", "120"])
    processes.append(session)
    up, _key = previous(processes, manager.state_file, model)
    held(manager.state_file, up, {"lease-of-a-live-session": [session.pid, "a claude session"]})
    entries = read_json(index(), {})
    entries[str(up.port)]["at"] += 600
    write_json(index(), entries)
    with pytest.raises(ServerFailed, match="did not start between"):
        manager.lease(asked(model, up.port))
    assert up.process.poll() is None

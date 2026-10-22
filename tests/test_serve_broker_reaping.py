"""The broker waits on every server it started, however the server ends, and says how it ended.

A child that has exited and is not waited on stays in the process table as a defunct entry, which a
process scan counts as running compute. Every server here is a real process (`canned_binary`, which
carries the markers and is no model) started by a real manager, and the defunct check looks at the
real process table.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import psutil
import pytest

from poolhouse.platform import end_child, exit_said
from poolhouse.serve.process import kill_process_tree
from tests.test_serve_broker import ask, broker, holders, llama_binary, models

__all__ = ["broker", "holders", "llama_binary", "models"]
pytest_plugins = ["serve_node"]
pytestmark = pytest.mark.usefixtures("serve_leases")


def defunct() -> list[int]:
    """The pids of this process's children that have exited and not been waited on."""
    return [one.pid for one in psutil.Process().children() if one.status() == psutil.STATUS_ZOMBIE]


def killed_from_outside(pid: int) -> None:
    """SIGKILL ``pid`` from here, as a person's ``kill -9`` would, and wait until it is a corpse."""
    os.kill(pid, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while pid not in defunct():
        assert time.monotonic() < deadline, "the killed server never became a corpse"
        time.sleep(0.02)


def test_a_server_killed_from_outside_is_waited_on_and_reported_with_its_exit_status(broker, models, holders):
    said: list[str] = []
    broker.say = said.append
    grant = broker.lease(ask(models[0], holders().pid), timeout=30)
    pid = broker.servers[grant.port].pid
    killed_from_outside(pid)

    broker.reap()

    assert pid not in defunct() and not psutil.pid_exists(pid)
    assert grant.port not in broker.servers
    assert [(one["pid"], one["code"], one["said"], one["expected"]) for one in broker.snapshot()["exits"]] \
        == [(pid, -signal.SIGKILL, "killed by SIGKILL", False)]
    assert any(f"port {grant.port}" in line and "died on its own" in line and "SIGKILL" in line for line in said)
    assert str(grant.port) not in broker.manager._load(), "the dead server is still on the lease record"


def test_a_server_the_broker_stops_is_waited_on_and_its_exit_status_kept(broker, models, holders):
    grant = broker.lease(ask(models[0], holders().pid), timeout=30)
    pid = broker.servers[grant.port].pid

    assert broker.stop(grant.port, force=True)["stopped"]

    assert pid not in defunct() and not psutil.pid_exists(pid)
    assert [(one["pid"], one["said"], one["expected"]) for one in broker.snapshot()["exits"]] \
        == [(pid, "killed by SIGTERM", True)]


def test_a_server_evicted_to_make_room_is_waited_on(broker, models, holders):
    first = broker.lease(ask(models[0], holders().pid), timeout=30)
    pid = broker.servers[first.port].pid
    broker.release(first.lease)

    second = broker.lease(ask(models[1], holders().pid), timeout=30)

    assert second.port != first.port or broker.servers[second.port].pid != pid
    assert pid not in defunct() and not psutil.pid_exists(pid)
    assert [one["pid"] for one in broker.snapshot()["exits"] if one["expected"]] == [pid]


def test_an_idle_server_the_broker_reaps_is_waited_on(broker, models, holders):
    grant = broker.lease(ask(models[0], holders().pid), timeout=30)
    pid = broker.servers[grant.port].pid
    broker.release(grant.lease)
    broker.idle_s, broker.busy = 0.0, lambda url: False
    time.sleep(0.05)

    assert [one.port for one in broker.reap()] == [grant.port]

    assert pid not in defunct() and not psutil.pid_exists(pid)
    assert [one["pid"] for one in broker.snapshot()["exits"]] == [pid]


def test_dropping_the_lease_of_a_server_that_already_died_still_waits_on_it(broker, models, holders):
    from poolhouse.serve import ServerSpec
    from poolhouse.serve.ports import free_port

    info = broker.start(ServerSpec(model=models[0], port=free_port(), context=512), timeout=30)
    killed_from_outside(info.pid)
    broker.reap()

    broker.drop(info)

    assert info.pid not in defunct() and not psutil.pid_exists(info.pid)


def test_every_server_a_manager_closes_is_waited_on(broker, models, holders):
    first = broker.lease(ask(models[0], holders().pid), timeout=30)
    second = broker.lease(ask(models[1], holders().pid, purpose="other"), timeout=30)
    pids = [broker.servers[first.port].pid, broker.servers[second.port].pid]

    broker.manager.close()

    assert not set(pids) & set(defunct()) and not any(psutil.pid_exists(pid) for pid in pids)


def test_a_detached_server_that_dies_later_is_still_waited_on(broker, models):
    from poolhouse.serve import ServerSpec
    from poolhouse.serve.ports import free_port

    manager = broker.manager
    info = manager.lease(ServerSpec(model=models[0], port=free_port(), context=512), roam=False, timeout=30,
                         check_flags=False, preflight=False, warmup_request=False)
    manager.detach(info)
    manager.close()
    assert psutil.pid_exists(info.pid) and info.pid not in defunct(), "close() stopped a detached server"
    killed_from_outside(info.pid)

    broker.reap()

    assert info.pid not in defunct() and not psutil.pid_exists(info.pid)


def test_ending_a_child_that_ignores_the_request_kills_it_and_waits_for_it(tmp_path):
    stubborn = subprocess.Popen([sys.executable, "-c", (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True)\n"
        "time.sleep(60)")], stdout=subprocess.PIPE, text=True)
    assert stubborn.stdout.readline().strip() == "up"

    code = end_child(stubborn, grace_s=0.3)

    assert code == -signal.SIGKILL and exit_said(code) == "killed by SIGKILL"
    assert stubborn.pid not in defunct() and not psutil.pid_exists(stubborn.pid)


def test_ending_a_child_that_already_exited_returns_its_real_status():
    quick = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"])
    deadline = time.monotonic() + 10
    while quick.pid not in defunct():
        assert time.monotonic() < deadline
        time.sleep(0.02)

    assert end_child(quick) == 7 and exit_said(7) == "exited with status 7"
    assert quick.pid not in defunct()


def test_a_server_that_never_answers_is_waited_on_when_the_start_gives_up(tmp_path, models):
    """A plain child that binds nothing stands in for a server that hangs while loading."""
    from poolhouse.serve import LlamaServerBackend, ServerFailed, ServerManager, ServerSpec
    from poolhouse.serve.broker import Broker
    from poolhouse.serve.ports import free_port

    silent = tmp_path / "silent-server"
    silent.write_text("#!/bin/sh\nexec sleep 60\n")
    silent.chmod(0o755)
    hanging = Broker(ServerManager(LlamaServerBackend(binary=silent), state_file=tmp_path / "servers.json"),
                     room=lambda: None, scan=lambda: [])
    before = {one.pid for one in psutil.Process().children()}

    with pytest.raises(ServerFailed, match="did not become healthy"):
        hanging.start(ServerSpec(model=models[0], port=free_port(), context=512), timeout=1.0,
                      options={"roam": False, "check_flags": False, "preflight": False, "warmup_request": False})

    assert {one.pid for one in psutil.Process().children()} - before == set(), \
        "the server that never answered, or its watchdog, is still in the process table"


def test_the_queue_view_says_which_servers_died_on_their_own(broker, models, holders, capsys, monkeypatch):
    import argparse

    from poolhouse.serve import broker_cli, broker_wire

    grant = broker.lease(ask(models[0], holders().pid), timeout=30)
    killed_from_outside(broker.servers[grant.port].pid)
    broker.reap()
    monkeypatch.setattr(broker_wire, "status", lambda: {**broker.snapshot(), "runtime": {}})

    assert broker_cli.cmd_queue(argparse.Namespace(json=False)) == 0

    assert f"ended :{grant.port}" in capsys.readouterr().out


@pytest.mark.slow
def test_the_machine_s_broker_leaves_no_defunct_server_behind_a_kill_and_reports_it(llama_binary, models,
                                                                                    monkeypatch):
    """The incident: a server killed from outside under a running broker daemon stayed defunct."""
    import json

    from poolhouse.serve import broker_wire

    monkeypatch.setenv("LLAMA_CPP_SERVER", str(llama_binary))
    got = broker_wire.lease("chat", [models[0]], reason="reaping test", spec={"context": 512}, timeout=60)
    daemon = json.loads(broker_wire.record_path().read_text())["pid"]
    try:
        pid = next(one["pid"] for one in broker_wire.status()["servers"] if one["port"] == got.port)
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 20
        while psutil.pid_exists(pid):
            assert time.monotonic() < deadline, f"pid {pid} is still in the process table: nobody waited on it"
            time.sleep(0.1)
        exits = broker_wire.status()["exits"]
        assert [(one["pid"], one["said"], one["expected"]) for one in exits] == [(pid, "killed by SIGKILL", False)]
    finally:
        for one in broker_wire.status().get("servers", []):
            if one["pid"]:
                kill_process_tree(one["pid"])
        kill_process_tree(daemon)


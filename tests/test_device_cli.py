"""Device CLI keeps the existing authenticated availability API and targeting."""
from __future__ import annotations

import argparse
import threading

import pytest

from poolhouse import cli
from poolhouse.fleet import device_cli, join, members
from poolhouse.fleet.api import Daemon, make_handler
from poolhouse.fleet.availability import Availability
from poolhouse.fleet.discovery import derive_token
from poolhouse.fleet.jobs import JobRunner
from poolhouse.http import Server


@pytest.fixture
def device(tmp_path, monkeypatch):
    key = b"controlled-test-pool-key"
    monkeypatch.setattr(device_cli, "_require_key", lambda path: key)
    runner = JobRunner(tmp_path)
    stopped = []
    monkeypatch.setattr(runner, "stop_running", lambda: stopped.append(True))
    schedule = Availability()
    servers = []

    def start(policy):
        server = Server(("127.0.0.1", 0), make_handler(Daemon(
            runner, tmp_path, derive_token(key), schedule=schedule, on_paused=policy,
            report=lambda: {})))
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01})
        worker.start()
        servers.append((server, worker))
        return str(server.server_address[1])

    yield start, schedule, stopped
    for server, worker in servers:
        server.shutdown()
        server.server_close()
        worker.join()
    runner.shutdown()


@pytest.mark.parametrize("policy,expected", [("stop", [True]), ("finish", [])])
def test_pause_and_resume_preserve_the_actual_server_running_job_policy(device, policy, expected):
    start, schedule, stopped = device
    port = start(policy)
    assert cli.main(["device", "pause", "--port", port, "--reason", "desk work"]) == 0
    assert schedule.paused and stopped == expected
    assert cli.main(["device", "resume", "--port", port]) == 0
    assert not schedule.paused and stopped == expected


def test_schedule_read_and_named_operations_use_the_same_authenticated_api(device):
    start, schedule, stopped = device
    port = start("stop")
    for operation in ([], ["busy", "mon-fri 09:00-17:00"], ["free", "mon-fri 12:00-13:00"]):
        assert cli.main(["device", "schedule", *operation, "--port", port]) == 0
    assert [window.busy for window in schedule.windows] == [True, False]
    assert cli.main(["device", "schedule", "clear", "--port", port]) == 0
    assert schedule.windows == [] and stopped == []


def test_bad_authentication_cannot_change_schedule_or_stop_work(device, monkeypatch):
    start, schedule, stopped = device
    port = start("stop")
    monkeypatch.setattr(device_cli, "_require_key", lambda path: b"wrong-test-key")
    assert cli.main(["device", "pause", "--port", port]) == 2
    assert not schedule.paused and stopped == []


def test_named_target_reuses_current_pool_discovery(monkeypatch):
    key = b"current-pool-test-key"
    selected = object()
    seen = []
    monkeypatch.setattr(device_cli, "_require_key", lambda path: key)
    monkeypatch.setattr(device_cli.Member, "find_one", lambda **kwargs: seen.append(kwargs) or selected)
    args = argparse.Namespace(pool_key="selected-pool", name="test-device", port=9999)
    assert device_cli._member(args) is selected
    assert seen[0]["name"] == "test-device" and seen[0]["discovery"].key == key


@pytest.mark.parametrize("argv", [["schedule", "busy"], ["schedule", "free"], ["schedule", "clear", "hours"]])
def test_invalid_schedule_never_resolves_a_device(monkeypatch, argv):
    def forbidden(*args):
        raise AssertionError("invalid input reached discovery")
    monkeypatch.setattr(device_cli, "_member", forbidden)
    assert device_cli.main(argv) == 2


@pytest.mark.parametrize("word", ["pause", "resume", "when", "busy"])
def test_members_has_no_availability_alias(word):
    with pytest.raises(SystemExit) as refused:
        members.main([word])
    assert refused.value.code == 2


def test_pool_list_replaces_the_old_plural_command():
    with pytest.raises(SystemExit) as refused:
        join.main(["pools"])
    assert refused.value.code == 2

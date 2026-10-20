"""`poolhouse runtime restart-host`: the running host is replaced by the selected runtime, gracefully or not at all.

Host branch tests use the verified Runtime descriptor seam and real loopback HTTP health.
One replacement integration retains real immutable runtime building, verification and selection;
the deployment suite separately drives build, smoke, recovery and rollback.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_runtime_deploy import builder, commit, plan_for, world  # noqa: F401

from poolhouse import runtime, runtime_deploy, runtime_host
from poolhouse.http import Server

pytestmark = pytest.mark.slow
pytest_plugins = ["node_kit"]
OLD = "0" * 40


class Host:
    """A stand-in daemon: answers /health with the commit it runs and, when it has one, its launcher-control instance."""

    def __init__(self, commit_id: str, control: str = "a" * 32) -> None:
        chosen = runtime.available()
        prefix = chosen.prefix if chosen else Path("/unselected")
        self.say = {"commit": commit_id, "name": "host", **({"launcher_control": control} if control else {}),
                    "runtime_prefix_digest": hashlib.sha256(str(prefix.resolve()).encode()).hexdigest()}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(outer.say).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def host():
    made = []

    def start(commit_id=OLD, **kw):
        made.append(Host(commit_id, **kw))
        return made[-1]

    yield start
    for one in made:
        one.stop()


@pytest.fixture
def installed_runtime(world):  # noqa: F811
    repo, launchers, _ = world
    head = commit(repo, "a")
    outcome = runtime_deploy.ensure(plan_for(repo, launchers), builder=builder())
    assert outcome.action == "switched"
    chosen = runtime.available()
    assert chosen is not None and chosen.commit == head and chosen.python.is_file()
    return chosen


@pytest.fixture
def selected_runtime(tmp_path, monkeypatch):
    """Supply the verified descriptor consumed by restart, without rebuilding a wheel."""
    prefix = tmp_path / "selected-runtime"
    prefix.mkdir()
    chosen = runtime.Runtime(prefix, "1" * 40, "0.1.0", runtime.identity())
    monkeypatch.setattr(runtime, "available", lambda: chosen)
    return chosen


class Spawner:
    def __init__(self, serving=None, becomes=""):
        self.calls, self.serving, self.becomes = [], serving, becomes

    def __call__(self, module, argv, *, log):
        self.calls.append((module, list(argv), Path(log)))
        if self.serving is not None and self.becomes:
            self.serving.say["commit"] = self.becomes
            self.serving.say["launcher_control"] = "b" * 32


def test_no_selected_runtime_means_nothing_to_restart_onto(host, monkeypatch):
    monkeypatch.setattr(runtime, "available", lambda: None)
    spawn = Spawner()
    out = runtime_host.restart(host().port, Path("/r"), seams=runtime_host.Seams(spawn=spawn))
    assert out.action == "failed" and "no verified runtime" in out.detail and not spawn.calls


def test_a_host_that_is_not_running_is_left_for_the_next_start_to_pick_the_runtime(selected_runtime):
    spawn = Spawner()
    free = Host(OLD)
    port = free.port
    free.stop()
    out = runtime_host.restart(port, Path("/r"), seams=runtime_host.Seams(spawn=spawn))
    assert (out.action, out.commit) == ("not-running", selected_runtime.commit) and not spawn.calls


def test_a_host_already_on_the_selected_runtime_is_not_restarted_unless_forced(selected_runtime, host):
    running, spawn = host(selected_runtime.commit), Spawner()
    assert runtime_host.restart(running.port, Path("/r"), seams=runtime_host.Seams(spawn=spawn)).action == "current" and not spawn.calls
    assert runtime_host.restart(running.port, Path("/r"), seams=runtime_host.Seams(spawn=spawn), force=True, wait_s=0).action == "restarting"
    assert len(spawn.calls) == 1


def test_a_host_with_no_launcher_control_is_refused_and_never_signalled(selected_runtime, host):
    running, spawn = host(control=""), Spawner()
    out = runtime_host.restart(running.port, Path("/r"), seams=runtime_host.Seams(spawn=spawn))
    assert out.action == "failed" and "predates" in out.detail and "Poolhouse" in out.detail and not spawn.calls


def test_a_behind_host_is_replaced_through_the_job_preserving_restart_on_its_port_and_root(installed_runtime, host):
    assert runtime.available() == installed_runtime
    running = host()
    spawn = Spawner(serving=running, becomes=installed_runtime.commit)
    out = runtime_host.restart(running.port, Path("/the/root"), seams=runtime_host.Seams(spawn=spawn), wait_s=10)
    assert (out.action, out.commit) == ("restarted", installed_runtime.commit)
    [(module, argv, log)] = spawn.calls
    assert module == "poolhouse.fleet.launch"
    assert argv == ["--restart", "--no-browser", "--port", str(running.port), "--root", "/the/root"]
    assert log == runtime.directory() / "restart.log"

    assert runtime.available() == installed_runtime

def test_a_replacement_that_does_not_show_up_is_reported_not_forced(selected_runtime, host):
    running, spawn = host(), Spawner()
    out = runtime_host.restart(running.port, Path("/r"), seams=runtime_host.Seams(spawn=spawn), wait_s=1)
    assert out.action == "failed" and "still runs" in out.detail and len(spawn.calls) == 1


def test_a_held_port_that_missed_the_health_probe_is_not_called_not_running(selected_runtime):
    import socket
    spawn = Spawner()
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(4)
        seams = runtime_host.Seams(spawn=spawn, health=lambda _port: None)
        out = runtime_host.restart(silent.getsockname()[1], Path("/r"), seams=seams)
    assert out.action == "failed" and "holds port" in out.detail and not spawn.calls


def test_forced_same_commit_restart_waits_for_new_generation_and_selected_prefix(selected_runtime, host, monkeypatch):
    chosen = runtime.available()
    selected = hashlib.sha256(str(chosen.prefix.resolve()).encode()).hexdigest()
    old = {"commit": selected_runtime.commit, "launcher_control": "a" * 32, "runtime_prefix_digest": selected}
    replacement = {**old, "launcher_control": "b" * 32}
    replies = iter([old, old, None, replacement])
    seen = []
    def health(_port):
        value = next(replies)
        seen.append(value)
        return value
    clock = [0.0]
    monkeypatch.setattr(runtime_host, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    spawn = Spawner()
    result = runtime_host.restart(12345, Path("/r"), seams=runtime_host.Seams(spawn=spawn, health=health), force=True, wait_s=2)
    assert result.action == "restarted"
    assert seen == [old, old, None, replacement]
    assert len(spawn.calls) == 1


@pytest.mark.parametrize('change', [
    {'launcher_control': 'a' * 32}, {'launcher_control': None}, {'launcher_control': 'not-a-generation'},
    {'runtime_prefix_digest': None}, {'runtime_prefix_digest': 'e' * 64},
    {'commit': 'e' * 40}, {'commit': 'f' * 8}, {'commit': 'f' * 40 + ' (dirty)'},
])
def test_wait_never_accepts_stale_or_unproven_replacement(change, monkeypatch):
    target = runtime_host._Target('f' * 40, 'd' * 64, 'a' * 32)
    replacement = {'commit': 'f' * 40, 'runtime_prefix_digest': 'd' * 64, 'launcher_control': 'b' * 32, **change}
    clock = [0.0]
    monkeypatch.setattr(runtime_host, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    assert not runtime_host._waited(12345, target, 0.5, lambda _port: replacement)
    assert clock[0] == 0.5


def test_daemon_health_proves_its_actual_interpreter_prefix_without_exposing_path(monkeypatch):
    import sys

    from poolhouse.fleet import daemon

    state = SimpleNamespace(base_report=lambda: {}, labels=[], bench_host=SimpleNamespace(report=lambda: {}),
                            control=SimpleNamespace(instance='a' * 32), heard=[])
    monkeypatch.setattr(daemon.updating, 'state', lambda: {})
    monkeypatch.setattr(daemon.autostart_check, 'state_word', lambda: 'off')
    health = daemon.DaemonRuntime.report(state)
    assert health['runtime_prefix_digest'] == hashlib.sha256(str(Path(sys.prefix).resolve()).encode()).hexdigest()
    assert str(Path(sys.prefix)) not in json.dumps(health)

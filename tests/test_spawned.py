"""A process started and then waited for fails the wait the moment it cannot come up.

Every test starts a real child process. Before this, a broker that raised at startup, or was
killed, left its client polling for a record that never came until the whole timeout ran out.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse import home, jobs, node_launch, runtime, runtime_host, spawned
from poolhouse.fleet import launch
from poolhouse.serve import broker_wire, holding
from poolhouse.serve.backend import ServerSpec
from poolhouse.serve.broker import BrokerError

ROOT = Path(__file__).resolve().parent.parent
QUICK_S = 8.0
"""A failure that was waiting out a 30 to 120 second timeout must arrive well inside this."""


def child(code: str, **kw) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL, **kw)


def reap(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


@pytest.fixture
def src(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "src"))


def never() -> None:
    return None


# ------------------------------------------------------------------------------ the helper
def test_a_child_whose_startup_raises_fails_the_wait_with_the_cause(tmp_path):
    failure = tmp_path / "failure.json"
    code = ("import sys; from pathlib import Path; from poolhouse import spawned\n"
            f"with spawned.Failing(Path({str(failure)!r})):\n"
            "    raise RuntimeError('the state directory is unreadable')\n")
    started = time.time()
    proc = child(code, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                 stderr=subprocess.DEVNULL)
    try:
        with pytest.raises(spawned.StartFailed) as raised:
            spawned.wait(proc, never, what="the broker", timeout_s=60.0,
                         watch=spawned.Watch(failure=lambda: spawned.failure_in(failure, started)))
    finally:
        reap(proc)
    assert time.time() - started < QUICK_S
    assert raised.value.kind == "crashed"
    assert "RuntimeError: the state directory is unreadable" in str(raised.value)
    record = json.loads(failure.read_text())
    assert record["pid"] == proc.pid and "RuntimeError" in record["traceback"] and record["version"] == 1


def test_a_child_killed_before_it_registers_fails_the_wait_at_once_naming_the_signal():
    proc = child("import time; time.sleep(60)")
    proc.send_signal(signal.SIGKILL)
    started = time.monotonic()
    try:
        with pytest.raises(spawned.StartFailed) as raised:
            spawned.wait(proc, never, what="the broker", timeout_s=60.0)
    finally:
        reap(proc)
    assert time.monotonic() - started < QUICK_S
    assert raised.value.kind == "exited" and "was killed by SIGKILL" in str(raised.value)


def test_a_child_that_exits_with_an_error_names_its_status_and_the_end_of_its_log(tmp_path):
    log = tmp_path / "child.log"
    with log.open("wb") as out:
        proc = child("print('loading'); raise SystemExit('no port is free')", stdout=out, stderr=out)
    try:
        with pytest.raises(spawned.StartFailed) as raised:
            spawned.wait(proc, never, what="the daemon", timeout_s=60.0,
                         watch=spawned.Watch(log=log))
    finally:
        reap(proc)
    said = str(raised.value)
    assert "exited with status 1" in said and "no port is free" in said and str(log) in said


def test_a_slow_start_that_is_healthy_succeeds_without_a_false_failure(tmp_path):
    record = tmp_path / "record.json"
    code = f"import time; time.sleep(1.5); open({str(record)!r}, 'w').write('{{\"up\": 1}}'); time.sleep(5)"
    proc = child(code)
    failure = tmp_path / "failure.json"
    try:
        got = spawned.wait(proc, lambda: json.loads(record.read_text()) if record.exists() else None,
                           what="the broker", timeout_s=30.0,
                           watch=spawned.Watch(failure=lambda: spawned.failure_in(failure, time.time())))
    finally:
        reap(proc)
    assert got == {"up": 1}


def test_a_child_that_registers_and_exits_at_once_is_a_success(tmp_path):
    record = tmp_path / "record.json"
    proc = child(f"open({str(record)!r}, 'w').write('1')")
    try:
        got = spawned.wait(proc, lambda: True if record.exists() else None, what="the broker",
                           timeout_s=30.0)
    finally:
        reap(proc)
    assert got is True


def test_a_running_child_that_never_answers_times_out_and_says_it_is_still_running():
    proc = child("import time; time.sleep(60)")
    try:
        with pytest.raises(spawned.StartFailed) as raised:
            spawned.wait(proc, never, what="the broker", timeout_s=0.5, watch=spawned.Watch(poll_s=0.05))
    finally:
        reap(proc)
    assert raised.value.kind == "timeout" and "still running" in str(raised.value)


def test_a_launcher_that_exits_cleanly_is_waited_past_when_asked(tmp_path):
    proc = child("pass")
    proc.wait(timeout=10)
    with pytest.raises(spawned.StartFailed) as raised:
        spawned.wait(proc, never, what="the daemon", timeout_s=0.4,
                     watch=spawned.Watch(poll_s=0.05, clean_exit_ends=False))
    assert raised.value.kind == "exited" and "exited with status 0" in str(raised.value)
    bad = child("raise SystemExit(2)", stderr=subprocess.DEVNULL)
    bad.wait(timeout=10)
    with pytest.raises(spawned.StartFailed) as ended:
        spawned.wait(bad, never, what="the daemon", timeout_s=30.0,
                     watch=spawned.Watch(clean_exit_ends=False))
    assert ended.value.kind == "exited" and "status 2" in str(ended.value)


def test_a_failure_record_from_before_this_start_is_not_its_cause(tmp_path):
    failure = tmp_path / "failure.json"
    failure.write_text(json.dumps({"time": time.time() - 100, "type": "OSError", "cause": "old"}))
    assert spawned.failure_in(failure, time.time()) == ""
    assert "OSError: old" in spawned.failure_in(failure, time.time() - 1000)


def test_a_deliberate_exit_leaves_no_failure_record(tmp_path):
    failure = tmp_path / "failure.json"
    with pytest.raises(SystemExit), spawned.Failing(failure):
        raise SystemExit(0)
    assert not failure.exists()


# ------------------------------------------------------------------------------ the broker
@pytest.mark.usefixtures("src")
def test_a_broker_whose_startup_raises_fails_the_client_with_the_cause_in_seconds():
    """A real broker process cannot take its lock because the lock path is a directory."""
    home.state("broker.lock").mkdir(parents=True)
    started = time.monotonic()
    with pytest.raises(BrokerError) as raised:
        broker_wire.call("ping")
    took = time.monotonic() - started
    said = str(raised.value)
    assert took < QUICK_S, f"waited {took:.1f}s"
    assert "the broker stopped while starting" in said and "Directory" in said
    assert str(broker_wire.log_path()) in said


@pytest.mark.usefixtures("src")
def test_a_broker_that_raises_after_taking_its_lock_names_the_failing_write():
    """The record path is a directory, so the write that registers the broker raises."""
    broker_wire.record_path().mkdir(parents=True)
    started = time.monotonic()
    with pytest.raises(BrokerError) as raised:
        broker_wire.call("ping")
    assert time.monotonic() - started < QUICK_S
    assert "stopped while starting" in str(raised.value)
    recorded = json.loads(broker_wire.failure_path().read_text())
    assert recorded["type"] and "broker.json" in recorded["traceback"]


@pytest.mark.usefixtures("src")
def test_a_broker_killed_before_it_registers_fails_the_client_at_once(monkeypatch):
    real = jobs.detach

    def killed(*args, **kwargs):
        ran = real(*args, **kwargs)
        os.kill(ran.pid, signal.SIGKILL)
        return ran

    monkeypatch.setattr(jobs, "detach", killed)
    started = time.monotonic()
    with pytest.raises(BrokerError) as raised:
        broker_wire.call("ping")
    assert time.monotonic() - started < QUICK_S
    assert "was killed by SIGKILL" in str(raised.value)


@pytest.mark.usefixtures("src")
def test_two_clients_starting_the_broker_at_once_both_get_it():
    """The second client's broker finds the lock taken and exits cleanly; its client waits for the first's."""
    import threading

    from poolhouse.serve.process import kill_process_tree

    results: list = []
    gate = threading.Barrier(2)

    def ask() -> None:
        gate.wait()
        try:
            results.append(broker_wire.call("ping"))
        except BrokerError as exc:
            results.append(exc)

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for one in threads:
        one.start()
    for one in threads:
        one.join(timeout=60)
    try:
        assert [type(r) for r in results] == [dict, dict], results
        assert results[0]["pid"] == results[1]["pid"]
    finally:
        pid = json.loads(broker_wire.record_path().read_text())["pid"]
        kill_process_tree(pid)


# --------------------------------------------------------------------------------- `up`
def test_up_fails_at_once_when_the_holder_it_started_dies(tmp_path, monkeypatch):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF" + b"\0" * 64)
    log = holding.log_path("x")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("Traceback\nSystemError: error return without exception set\n")
    dying = child("import time; time.sleep(0.3); raise SystemExit(3)", stderr=subprocess.DEVNULL)
    monkeypatch.setattr(holding, "_detach", lambda hold_id, manager: dying)
    monkeypatch.setattr(holding, "log_path", lambda hold_id: log)
    manager = SimpleNamespace(backend=SimpleNamespace(), broker=None)
    spec = ServerSpec(model=str(model), context=4096, parallel=1)
    started = time.monotonic()
    try:
        with pytest.raises(holding.Refusal) as raised:
            holding.up(spec, manager=manager, terms=holding.Terms(patience_s=120.0), say=lambda _l: None)
    finally:
        reap(dying)
    assert time.monotonic() - started < QUICK_S
    assert "the lease holder stopped while starting" in str(raised.value)
    assert "exited with status 3" in str(raised.value) and "SystemError" in str(raised.value)
    assert holding.holds() == []


# ----------------------------------------------------------------- the other waits on a start
def test_a_node_supervisor_that_ends_fails_the_start_with_its_status(tmp_path, monkeypatch):
    dying = child("raise SystemExit(4)", stderr=subprocess.DEVNULL)
    monkeypatch.setattr(node_launch, "supervised", lambda state: False)
    monkeypatch.setattr(node_launch, "_start_supervisor", lambda state, extra: dying)
    monkeypatch.setattr(node_launch.node_supervise, "resolve", lambda state: None)
    monkeypatch.setattr(node_launch, "node_health", lambda state: None)
    started = time.monotonic()
    try:
        with pytest.raises(node_launch.NodeUnavailable) as raised:
            node_launch.ensure_node(tmp_path / "node", extra=[], wait_s=60.0)
    finally:
        reap(dying)
    assert time.monotonic() - started < QUICK_S
    assert "exited with status 4" in str(raised.value)


def test_a_node_supervisor_that_keeps_running_is_still_waited_for(tmp_path, monkeypatch):
    running = child("import time; time.sleep(30)")
    monkeypatch.setattr(node_launch, "supervised", lambda state: False)
    monkeypatch.setattr(node_launch, "_start_supervisor", lambda state, extra: running)
    monkeypatch.setattr(node_launch.node_supervise, "resolve", lambda state: None)
    answers = iter([None, None, None, {"pid": 1}])
    monkeypatch.setattr(node_launch, "node_health", lambda state: next(answers, {"pid": 1}))
    try:
        assert node_launch.ensure_node(tmp_path / "node", extra=[], wait_s=30.0) == {"pid": 1}
    finally:
        reap(running)


def test_a_restart_process_that_fails_ends_the_host_wait_with_its_status(tmp_path, monkeypatch):
    chosen = runtime.Runtime(tmp_path / "rt", "1" * 40, "0.1.0", runtime.identity())
    (tmp_path / "rt").mkdir()
    monkeypatch.setattr(runtime, "available", lambda: chosen)
    dying = child("raise SystemExit(5)", stderr=subprocess.DEVNULL)
    old = {"commit": "0" * 40, "launcher_control": "a" * 32}
    seams = runtime_host.Seams(spawn=lambda module, argv, *, log: dying, health=lambda port: old,
                               held=lambda port: False)
    started = time.monotonic()
    try:
        out = runtime_host.restart(8123, tmp_path, wait_s=60.0, seams=seams)
    finally:
        reap(dying)
    assert time.monotonic() - started < QUICK_S
    assert out.action == "failed" and "exited with status 5" in out.detail


def test_a_daemon_that_dies_ends_the_health_wait_with_its_status(monkeypatch):
    dying = child("raise SystemExit(6)", stderr=subprocess.DEVNULL)
    monkeypatch.setattr(launch, "_health", lambda port: None)
    started = time.monotonic()
    try:
        with pytest.raises(spawned.StartFailed) as raised:
            launch.wait_for_health(1, seconds=60.0, child=dying)
    finally:
        reap(dying)
    assert time.monotonic() - started < QUICK_S and "status 6" in str(raised.value)

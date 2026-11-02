"""Real sandbox pipes, bounded output and cancellation share parent-owned lifetimes."""
from __future__ import annotations

import os
import queue
import signal
import sys
import threading
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from poolhouse import sandbox
from poolhouse.sandbox import policies
from poolhouse.sandbox.io import CHUNK, IO, Pipes

pytest_plugins = ["tests.sandbox_kit"]


@pytest.fixture
def policy(tmp_path, seatbelt):
    return policies.bash(tmp_path, tmp_path, wall_seconds=5)




def shell(script, policy, **options):
    return sandbox.run(["/bin/sh", "-c", script], policy, **options)


def test_input_and_stdout_queue_preserve_the_complete_child_output(policy):
    value = b"0123456789" * 40000
    output = queue.Queue(maxsize=64)
    result = sandbox.run(["/bin/cat"], policy, io=IO(input=value, output=output))
    assert result.ok and not result.cancelled and result.stdout.encode() == value
    chunks = []
    while not output.empty():
        chunks.append(output.get_nowait())
    assert b"".join(chunks) == value


def test_private_directional_pipes_reach_only_the_explicit_child(policy):
    with Pipes.opened() as pipes:
        os.write(pipes.parent_write, b"hello\n")
        script = f'IFS= read -r -u {pipes.read} value; printf "reply:%s" "$value" >&{pipes.write}'
        result = shell(script, policy, io=IO(pipes=pipes))
        assert result.ok, result.stderr
        assert os.read(pipes.parent_read, 64) == b"reply:hello"
    for descriptor in (pipes.read, pipes.write, pipes.parent_read, pipes.parent_write):
        with pytest.raises(OSError):
            os.fstat(descriptor)
    with pytest.raises(ValueError, match="live factory"):
        pipes.check()


def test_input_only_worker_does_not_inherit_retained_ipc(policy):
    with Pipes.opened() as pipes:
        result = shell(f'printf leaked >&{pipes.write}', policy, io=IO(input=b"input"))
        assert not result.ok
        assert "Bad file descriptor" in result.stderr or "bad file descriptor" in result.stderr


def test_parent_pipe_ends_are_not_inherited(policy):
    with Pipes.opened() as pipes:
        result = shell(f'printf leaked >&{pipes.parent_write}', policy, io=IO(pipes=pipes))
        assert not result.ok
        assert "Bad file descriptor" in result.stderr or "bad file descriptor" in result.stderr


def test_cancellation_before_backend_setup_starts_nothing(monkeypatch):
    stopped = threading.Event()
    stopped.set()
    backend = Mock(side_effect=AssertionError("No backend or process should start"))
    monkeypatch.setitem(sandbox.run.__globals__, "backend", backend)
    result = sandbox.run(["not-started"], sandbox.Policy("cancelled"), io=IO(stop=stopped))
    assert result.cancelled and not result.ok
    backend.assert_not_called()


def test_cancel_interrupts_a_child_that_never_reads_its_full_input(policy):
    stopped = threading.Event()
    timer = threading.Timer(.1, stopped.set)
    timer.start()
    try:
        result = shell("while :; do sleep .01; done", policy, io=IO(input=b"x" * CHUNK * 8, stop=stopped))
    finally:
        timer.cancel()
        timer.join()
    assert result.cancelled and not result.timed_out


def test_output_backpressure_invalidates_before_returning(policy):
    stopped = threading.Event()
    with pytest.raises(RuntimeError, match="consumer stopped reading"):
        shell("yes output", policy, io=IO(output=queue.Queue(maxsize=1), stop=stopped))
    assert stopped.is_set()


def test_output_limit_remains_distinct_from_user_cancellation(policy):
    result = shell("yes output", replace(policy, limits=replace(policy.limits, output_bytes=4096)), io=IO())
    assert result.truncated and not result.cancelled and not result.ok


def test_caller_supplied_pipe_topology_is_not_accepted():
    with pytest.raises(TypeError, match=r"Pipes\.opened"):
        Pipes()
    with pytest.raises(ValueError, match="factory-owned"):
        object.__new__(Pipes).check()


def test_replacing_a_factory_endpoint_refuses_before_child_start(policy):
    with Pipes.opened() as pipes:
        os.close(pipes.read)
        with Path(os.devnull).open() as replacement:
            assert replacement.fileno() == pipes.read
            with pytest.raises(ValueError, match="private directional"):
                shell("touch escaped", policy, io=IO(pipes=pipes))
            assert not (Path(policy.write[0]) / "escaped").exists()


def test_unsupported_pipe_backend_refuses_before_creating_descriptors(monkeypatch):
    import poolhouse.sandbox.io as process_io
    monkeypatch.setattr(process_io.sys, "platform", "unsupported")
    monkeypatch.setattr(process_io.os, "pipe", Mock(side_effect=AssertionError("No descriptors")))
    with pytest.raises(ValueError, match="cannot retain"), Pipes.opened():
        pytest.fail("Unsupported IPC factory was admitted")


@pytest.mark.parametrize("bad", [queue.Queue(), queue.Queue(maxsize=True), object()])
def test_unbounded_or_forged_output_sink_is_rejected_before_spawn(bad):
    with pytest.raises(ValueError, match="bounded queue"):
        sandbox.run(["not-started"], sandbox.Policy("bad"), io=IO(output=bad))


def test_process_channels_never_accept_unsandboxed_execution(policy):
    with pytest.raises(sandbox.PolicyError, match="operating-system sandbox"):
        sandbox.run(["/bin/true"], policy, io=IO(), unsandboxed=sandbox.AllowUnsandboxed("test refusal"))


@pytest.mark.parametrize("phase", ["wrap", "started"])
def test_cancel_during_backend_setup_never_executes_the_child(policy, monkeypatch, tmp_path, phase):
    from poolhouse.sandbox.seatbelt import Seatbelt
    backend = Seatbelt()
    original = getattr(backend, phase)
    stopped = threading.Event()

    def cancelled(*args):
        result = original(*args)
        stopped.set()
        return result

    monkeypatch.setattr(backend, phase, cancelled)
    result = shell("touch should-not-exist", policy, cwd=tmp_path, via=backend, io=IO(stop=stopped))
    assert result.cancelled
    assert not (tmp_path / "should-not-exist").exists()


@pytest.mark.parametrize("kind", ["event", "input", "io"])
def test_forged_process_channels_refuse_before_backend(kind, monkeypatch):
    backend = Mock(side_effect=AssertionError("No child should start"))
    monkeypatch.setitem(sandbox.run.__globals__, "backend", backend)
    value = {"event": IO(stop=object()), "input": IO(input="text"), "io": object()}[kind]
    with pytest.raises(ValueError):
        sandbox.run(["not-started"], sandbox.Policy("invalid"), io=value)
    backend.assert_not_called()


def test_unavailable_backend_invalidates_shared_admission_event(policy, monkeypatch):
    from poolhouse.sandbox.backend import Availability
    from poolhouse.sandbox.seatbelt import Seatbelt
    backend = Seatbelt()
    monkeypatch.setattr(backend, "available", lambda: Availability(False, "unavailable"))
    stopped = threading.Event()
    with pytest.raises(sandbox.SandboxUnavailable):
        shell("touch should-not-exist", policy, via=backend, io=IO(stop=stopped))
    assert stopped.is_set()


def test_final_capture_overflow_is_not_a_successful_exit(policy):
    capped = replace(policy, limits=replace(policy.limits, output_bytes=10))
    result = shell("printf 01234567890123456789", capped, io=IO())
    assert result.truncated and not result.ok
    assert len(result.stdout) == 10


def test_detached_stdout_owner_cannot_keep_capture_threads_alive(tmp_path, seatbelt):
    from tests.sandbox_kit import policy as python_policy
    script = """
import os, sys, time
child = os.fork()
if child == 0:
    os.setsid()
    time.sleep(1)
    open(sys.argv[1], 'w').close()
    os._exit(0)
print(child, flush=True)
time.sleep(.1)
"""
    finished = tmp_path / "detached-finished"
    result = sandbox.run([sys.executable, "-I", "-c", script, str(finished)],
                         python_policy(read=[tmp_path], write=[tmp_path], python=True), io=IO())
    child = int(result.stdout.strip())
    try:
        assert result.ok
        os.kill(child, 0)
        assert not finished.exists()
        assert not any(isinstance(thread, (sandbox.run.__globals__["_Capture"], sandbox.run.__globals__["_Input"]))
                       for thread in threading.enumerate())
    finally:
        with suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)


def test_successive_process_lifetimes_do_not_cancel_their_parent(policy):
    parent_stop = threading.Event()
    first, second = IO(), IO()
    assert shell("printf first", policy, io=first).ok
    assert first.stop.is_set() and not second.stop.is_set() and not parent_stop.is_set()
    assert shell("printf second", policy, io=second).ok
    assert second.stop.is_set() and not parent_stop.is_set()


def test_actual_sandbox_descriptor_inspection_has_no_lock_sites():
    from gates import file_locks
    root = Path(__file__).resolve().parents[1]
    for relative in ("src/poolhouse/sandbox/io.py", "src/poolhouse/sandbox/run.py"):
        assert not file_locks.scan(root / relative, relative)

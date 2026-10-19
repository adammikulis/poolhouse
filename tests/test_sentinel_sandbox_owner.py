"""A sandbox event counts toward the score of the session whose tool call started the run, and of no other."""

from __future__ import annotations

import asyncio
import threading

import pytest

from poolhouse import sentinel
from poolhouse.sandbox import policies, run
from poolhouse.sentinel.adapters import sandbox_listener
from tests.sandbox_kit import UnreadableBackend


def blind_run(node, tmp_path):
    """A failed run whose refusals cannot be read, reported to ``node``'s sandbox listener."""
    return run(["/bin/sh", "-c", "exit 1"], policies.bash(str(tmp_path), str(tmp_path)), via=UnreadableBackend(),
               on_event=sandbox_listener(node))


def test_a_sandbox_event_counts_only_for_the_session_whose_tool_ran_it(tmp_path):
    node = sentinel.Sentinel(tmp_path / "sentinel")
    inside, leave = threading.Event(), threading.Event()

    def innocent():
        with node.running("s-innocent"):
            inside.set()
            leave.wait(10)

    thread = threading.Thread(target=innocent)
    thread.start()
    try:
        assert inside.wait(10)
        with node.running("s-hostile"):
            for _ in range(3):
                blind_run(node, tmp_path)
    finally:
        leave.set()
        thread.join()
    assert node.score.value("session", "s-hostile") == pytest.approx(3.0)
    assert node.score.value("session", "s-innocent") == 0
    assert not node.store.blocked("session", "s-innocent")


def test_a_sandbox_event_outside_any_tool_call_is_reported_and_counted_for_nobody(tmp_path):
    node = sentinel.Sentinel(tmp_path / "sentinel")
    seen: list[sentinel.Event] = []
    node.bus.subscribe(seen.append)
    done = threading.Event()

    def bystander():
        with node.running("s-bystander"):
            done.wait(10)

    thread = threading.Thread(target=bystander)
    thread.start()
    try:
        blind_run(node, tmp_path)
    finally:
        done.set()
        thread.join()
    assert [e.kind for e in seen if e.kind == "sandbox.denials-unavailable"] == ["sandbox.denials-unavailable"]
    assert node.score.value("session", "s-bystander") == 0


def test_a_sandbox_event_from_a_tool_run_on_a_worker_thread_counts_for_the_session_that_called_it(tmp_path):
    node = sentinel.Sentinel(tmp_path / "sentinel")

    async def call():
        with node.running("s-async"):
            await asyncio.to_thread(blind_run, node, tmp_path)

    asyncio.run(call())
    assert node.score.value("session", "s-async") == pytest.approx(1.0)


def test_a_session_of_another_sentinel_is_not_counted(tmp_path):
    ours, theirs = sentinel.Sentinel(tmp_path / "ours"), sentinel.Sentinel(tmp_path / "theirs")
    with theirs.running("s-theirs"):
        blind_run(ours, tmp_path)
    assert ours.score.value("session", "s-theirs") == 0 and theirs.score.value("session", "s-theirs") == 0

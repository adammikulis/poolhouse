"""A sandbox event counts toward the score of the session whose tool call started the run, and of no other."""

from __future__ import annotations

import asyncio
import contextvars
import logging
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


def test_a_tool_call_of_another_sentinel_started_inside_this_one_does_not_hide_it(tmp_path):
    ours, theirs = sentinel.Sentinel(tmp_path / "ours"), sentinel.Sentinel(tmp_path / "theirs")
    with ours.running("s-ours"), theirs.running("s-theirs"):
        blind_run(ours, tmp_path)
        assert ours.running_session() == "s-ours" and theirs.running_session() == "s-theirs"
    assert ours.score.value("session", "s-ours") == pytest.approx(1.0)
    assert ours.score.value("session", "s-theirs") == 0 == theirs.score.value("session", "s-theirs")


def test_a_nested_tool_call_of_the_same_sentinel_counts_for_the_innermost_and_the_outer_one_again_after(tmp_path):
    node = sentinel.Sentinel(tmp_path / "sentinel")
    with node.running("s-outer"):
        with node.running("s-inner"):
            blind_run(node, tmp_path)
        assert node.running_session() == "s-outer"
    assert node.running_session() == ""
    assert node.score.value("session", "s-inner") == pytest.approx(1.0) and node.score.value("session", "s-outer") == 0


def test_a_sandbox_event_from_a_thread_that_did_not_inherit_the_tool_call_is_said_to_count_for_nobody(tmp_path, caplog):
    node = sentinel.Sentinel(tmp_path / "sentinel")
    with caplog.at_level(logging.WARNING, logger="poolhouse.sentinel"), node.running("s-threaded"):
        thread = threading.Thread(target=blind_run, args=(node, tmp_path))
        thread.start()
        thread.join()
    assert node.score.value("session", "s-threaded") == 0
    assert "counts for nobody" in caplog.text and "s-threaded" in caplog.text


def test_a_sandbox_event_from_a_thread_given_the_context_of_the_tool_call_counts_for_it(tmp_path, caplog):
    node = sentinel.Sentinel(tmp_path / "sentinel")
    with caplog.at_level(logging.WARNING, logger="poolhouse.sentinel"), node.running("s-carried"):
        thread = threading.Thread(target=contextvars.copy_context().run, args=(blind_run, node, tmp_path))
        thread.start()
        thread.join()
    assert node.score.value("session", "s-carried") == pytest.approx(1.0)
    assert "counts for nobody" not in caplog.text


def test_a_sandbox_event_with_no_tool_call_running_is_not_said_to_count_for_nobody(tmp_path, caplog):
    node = sentinel.Sentinel(tmp_path / "sentinel")
    with caplog.at_level(logging.WARNING, logger="poolhouse.sentinel"):
        blind_run(node, tmp_path)
    assert "counts for nobody" not in caplog.text

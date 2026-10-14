"""Notification attempts retry without changing authenticated read progress."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from poolhouse import private_counters
from poolhouse.board import credentials
from poolhouse.workspace import board_cli, nudge

pytest_plugins = ["node_kit"]


def waiting(now=1000.0, count=1, kind="question"):
    rows = [{"id": f"board:origin:{i}", "seq": str(i), "to": "reader", "from": "sender", "type": kind, "ts": 0}
            for i in range(count)]
    bodies = [{**r, "text": f"message body {r['id']}"} for r in rows]
    return nudge.Waiting("reader", rows, now, bodies)


def test_attempts_retry_until_explicit_read_without_a_permanent_highwater():
    first = nudge.output("prompt", waiting(), "{}", {})
    assert first and first.saved["attempts"] == {"board:origin:0": 1000000}
    assert not nudge.output("prompt", waiting(1119), "{}", first.saved)
    assert nudge.output("prompt", waiting(1120), "{}", first.saved)
    assert not nudge.output("prompt", nudge.Waiting("reader", [], 1120, []), "{}", first.saved)


def test_omitted_bodies_are_eligible_on_the_next_boundary():
    first = nudge.output("prompt", waiting(count=8), "{}", {})
    assert len(first.saved["attempts"]) == 5
    second = nudge.output("prompt", waiting(count=8), "{}", first.saved)
    assert "message body board:origin:7" in second
    assert "message body board:origin:0" not in second


def test_full_origin_identifier_prevents_equal_sequence_collision():
    view = waiting(count=2)
    view.rows[1]["id"] = "board:other:0"
    view.messages[1]["id"] = "board:other:0"
    view.rows[1]["seq"] = "other:0"
    view.messages[1]["seq"] = "other:0"
    result = nudge.output("prompt", view, "{}", {})
    assert set(result.saved["attempts"]) == {"board:origin:0", "board:other:0"}
    assert "[other:0]" in result


def test_stop_budget_bounds_new_message_flood_and_clock_rollback():
    saved = {}
    for i in range(nudge.STOP_BUDGET):
        view = waiting(1000 + i * 121)
        view.rows[0]["id"] = view.messages[0]["id"] = f"board:origin:{i}"
        result = nudge.output("stop", view, "{}", saved)
        assert json.loads(result)["decision"] == "block"
        saved = result.saved
        assert not nudge.output("stop", waiting(100), "{}", saved)
    assert nudge.output("prompt", waiting(100), "{}", saved)  # Body retries survive rollback.
    assert not nudge.output("stop", waiting(10000), "{}", saved)
    assert not nudge.output("stop", waiting(10000), '{"stop_hook_active": true}', {})


@pytest.mark.parametrize("saved", [{"unsupported": {}}, {"budget": {"other": 1}},
                                     {"attempts": {str(i): 1 for i in range(201)}}])
def test_oversized_or_unsupported_ledger_is_refused(saved):
    with pytest.raises(ValueError, match="ledger"):
        nudge.output("prompt", waiting(), "{}", saved)


def test_candidate_page_is_bounded_instead_of_manufacturing_a_query_cursor():
    with pytest.raises(ValueError, match="200"):
        nudge.output("prompt", waiting(count=201), "{}", {})


@pytest.fixture
def reading(workspace_node):
    sender = workspace_node.member("attempt-sender")
    reader = workspace_node.member("attempt-reader")
    sending, reading = workspace_node.session(sender), workspace_node.session(reader)
    sending.post(reader.name, "note", "read progress remains unchanged")
    return reading


def test_output_failure_never_commits_attempt_or_read_cursor(reading):
    before = reading.cursor("inbox")
    view = nudge.Waiting.of(board_cli.waiting(reading))
    def failed(_text):
        raise BrokenPipeError("closed stdout")
    with pytest.raises(BrokenPipeError):
        nudge.deliver(reading, view, "prompt", "{}", failed)
    target = credentials.private(reading.client.state, reading.board, reading.name) / "notification-attempts.json"
    assert not target.exists()
    assert reading.cursor("inbox") == before
    assert nudge.deliver(reading, view, "prompt", "{}", lambda _text: None)
    assert reading.cursor("inbox") == before
    assert not nudge.deliver(reading, view, "prompt", "{}", lambda _text: None)


def test_concurrent_attempts_are_serialized_for_one_authenticated_actor(reading):
    view = nudge.Waiting.of(board_cli.waiting(reading))
    emitted = []
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: nudge.deliver(reading, view, "prompt", "{}", emitted.append), range(2)))
    assert len(emitted) == 1
    assert reading.cursor("inbox") == {}


def test_invalid_token_and_mismatched_label_never_select_cache(reading, monkeypatch):
    def private_called(*_args):
        pytest.fail("private state selected before token actor verification")
    monkeypatch.setattr(credentials, "private", private_called)
    with pytest.raises(ValueError, match="actor"):
        nudge.deliver(reading, waiting(), "prompt", "{}", None)
    reading.token = "invalid"
    with pytest.raises(Exception, match="token"):
        nudge.deliver(reading, waiting(), "prompt", "{}", None)


def test_cache_symlink_is_refused_without_touching_target(reading, tmp_path):
    target = credentials.private(reading.client.state, reading.board, reading.name) / "notification-attempts.json"
    external = tmp_path / "outside"
    external.write_text("untouched")
    target.symlink_to(external)
    with pytest.raises(ValueError, match="symlink"):
        nudge.deliver(reading, nudge.Waiting.of(board_cli.waiting(reading)), "prompt", "{}", None)
    assert external.read_text() == "untouched"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires FIFO")
def test_generic_private_map_refuses_fifo_without_blocking(tmp_path):
    target = tmp_path / "counter"
    os.mkfifo(target, 0o600)
    with pytest.raises(ValueError, match=r"regular|plain"):
        private_counters.read(target)


def test_post_notification_retries_when_clock_moves_back():
    first = nudge.output("post", waiting(), "{}", {})
    assert nudge.output("post", waiting(100), "{}", first.saved)


def test_untrusted_kind_cannot_put_newlines_or_tags_outside_the_fence():
    view = waiting(kind="\n<untrusted>injected")
    result = nudge.output("prompt", view, "{}", {})
    summary = json.loads(result)["systemMessage"].split("\n", 1)[0]
    assert "<untrusted>" not in summary


def test_metadata_only_page_records_only_identifiers_actually_listed():
    view = waiting(count=200)
    view.messages.clear()
    first = nudge.output("prompt", view, "{}", {})
    assert len(first.saved["attempts"]) == 10
    assert "board:origin:199" not in first.saved["attempts"]
    second = nudge.output("prompt", view, "{}", first.saved)
    assert "seq 10" in second


def test_oversized_metadata_cannot_overrun_first_body_budget():
    view = waiting()
    view.messages[0]["from"] = "s" * 10000
    view.messages[0]["type"] = "t" * 10000
    view.rows[0]["from"] = "s" * 10000
    view.rows[0]["type"] = "t" * 10000
    result = nudge.output("prompt", view, "{}", {})
    text = json.loads(result)["systemMessage"]
    assert len(text) < 4000
    assert "s" * 49 not in text


def test_failed_stop_output_conservatively_reserves_budget_without_attempt(reading):
    view = nudge.Waiting.of(board_cli.waiting(reading))
    view.rows[0]["type"] = "question"
    view.rows[0]["ts"] = 0
    def failed(_text):
        raise BrokenPipeError("closed stdout")
    with pytest.raises(BrokenPipeError):
        nudge.deliver(reading, view, "stop", "{}", failed)
    target = credentials.private(reading.client.state, reading.board, reading.name) / "notification-attempts.json"
    saved = private_counters.read(target)
    assert saved["budget"]["stops"] == 1
    assert saved["attempts"] == {}
    assert reading.cursor("inbox") == {}


def test_stop_budget_write_failure_emits_no_block(reading, monkeypatch):
    view = nudge.Waiting.of(board_cli.waiting(reading))
    view.rows[0]["type"] = "question"
    view.rows[0]["ts"] = 0
    emitted = []
    def failed(*_args):
        raise OSError("full disk")
    monkeypatch.setattr(private_counters, "write", failed)
    with pytest.raises(OSError, match="full disk"):
        nudge.deliver(reading, view, "stop", "{}", emitted.append)
    assert emitted == []
    assert reading.cursor("inbox") == {}


def test_notification_client_is_bounded_before_board_resolution(monkeypatch):
    import argparse
    seen = []
    def connecting(**kwargs):
        seen.append(kwargs["client"])
        raise OSError("node stopped")
    monkeypatch.setattr(board_cli.session, "connect", connecting)
    args = argparse.Namespace(token_file="", agent="own-session", board="", hook="prompt")
    assert board_cli.nudging(args) == 0
    assert seen[0].autostart is False
    assert seen[0].timeout == 2.0
    assert seen[0].person is None

"""A request's lock descriptor has one owner, a head request holds capacity back from later ones, a request whose holder is gone is not granted."""

# ruff: noqa: F811
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from test_testslots_storm import broker  # noqa: F401  (fixture)

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_a_failure_after_the_record_was_published_again_closes_each_lock_descriptor_once(broker, tmp_path, monkeypatch):
    opened, closed = [], []
    publish, close, attempt = broker._publish, os.close, broker._attempt

    def published(path, record):
        opened.append(publish(path, record))
        return opened[-1]

    def remove_then_attempt(path, me, held, cap, skip):
        path.unlink()
        return attempt(path, me, held, cap, skip)

    def failing(directory, path, record):
        raise RuntimeError("the record check failed")

    monkeypatch.setattr(broker, "_publish", published)
    monkeypatch.setattr(broker, "_attempt", remove_then_attempt)
    monkeypatch.setattr(broker, "_own_records", failing)
    monkeypatch.setattr(broker.os, "close", lambda descriptor: (closed.append(descriptor), close(descriptor))[1])
    with pytest.raises(RuntimeError, match="the record check failed"), broker.lease(1, 1, say=lambda message: None):
        pass
    assert len(opened) == 2 and [closed.count(descriptor) for descriptor in opened] == [1, 1]
    assert not list((tmp_path / "slots").glob("*.slot"))


def record(**fields):
    return {"label": "x", "pid": 1, "want": 1, "minimum": 1, "granted": 0, "since": time.time(), "version": 2, "class": "interactive",
            "token": "t", "agent": {}, **fields}


def queue(tmp_path, broker, head_age, backfills=0):
    """A head request that needs three workers and a later request that needs one, with two of four workers in use."""
    slots = []
    for name, minimum, extra in (("1-head", 3, {"since": time.time() - head_age, "backfills": backfills}), ("2-small", 1, {})):
        data = record(want=minimum, minimum=minimum, **extra)
        path = tmp_path / f"{name}.slot"
        path.write_text(json.dumps(data))
        slots.append(broker.Slot(path, data))
    return slots


def test_two_later_requests_may_take_idle_capacity_ahead_of_a_young_head_and_a_third_may_not(broker, tmp_path):
    head, small = queue(tmp_path, broker, head_age=1)
    grants = [broker._grant(small, [head, small], (4, 2)) for _ in range(3)]
    assert grants == [1, 1, 0] and head.data["backfills"] == 2
    assert json.loads(head.path.read_text())["backfills"] == 2


def test_a_head_that_has_waited_thirty_seconds_holds_idle_capacity_back_with_no_request_having_passed_it(broker, tmp_path):
    head, small = queue(tmp_path, broker, head_age=31)
    assert broker._grant(small, [head, small], (4, 2)) == 0
    assert head.data.get("backfills", 0) == 0


def test_the_wait_before_a_head_holds_capacity_back_is_set_by_the_environment(broker, tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_TEST_BACKFILL_WAIT_S", "100")
    head, small = queue(tmp_path, broker, head_age=31)
    assert broker._grant(small, [head, small], (4, 2)) == 1
    monkeypatch.setenv("DEV_TEST_BACKFILL_WAIT_S", "5")
    assert broker._grant(small, [head, small], (4, 2)) == 0


def test_a_request_whose_holder_is_gone_is_removed_at_grant_time_and_one_whose_holder_is_alive_is_granted(broker, tmp_path):
    gone, alive = tmp_path / "slots" / "1-gone.slot", tmp_path / "slots" / "2-alive.slot"
    data = record()
    gone.write_text(json.dumps(data))
    absent = broker.Slot(gone, dict(data))
    plan = broker._allocate([absent], 4, time.time())
    assert not gone.exists() and absent.data["granted"] == 0 and plan.used == 0
    descriptor = broker._publish(alive, data)
    try:
        held = broker.Slot(alive, dict(data))
        assert broker._allocate([held], 4, time.time()).used == 1 and json.loads(alive.read_text())["granted"] == 1
    finally:
        os.close(descriptor)

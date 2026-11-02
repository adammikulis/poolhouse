"""Coordinated pool pauses: which pause a starting machine takes, and how a pause reaches the node's devices.

The devices are real daemons over TLS pinned to the certificates the node vouches for (PLUMBING ONLY: the node's answer is canned
at its wire, as in tests/test_fleet_tls.py); the fan-out and the listing are in tests/test_fleet_roster.py.
"""

from __future__ import annotations

import math
import time

import pytest
from test_fleet_roster import free_port, serve
from test_fleet_tls import stop

from poolhouse.fleet import roster, tls
from poolhouse.fleet.availability import Availability
from poolhouse.fleet.compute_cli import table
from poolhouse.fleet.pausing import (
    POOL_PAUSE,
    UNHEARD,
    Fanout,
    adopt_pause,
    member_pause,
    pause_among,
    pause_fleet,
    pause_table,
)

pytest_plugins = ["test_fleet_tls"]


def _paused(name: str, until: float | None, reason: str = "", paused: bool = True) -> dict:
    """A member row shaped the way a device's health report carries one."""
    return {"name": name,
            "device": {"availability": {"paused": paused, "paused_until": until,
                                        "paused_reason": reason}}}


class TestWhichPauseIsAdopted:
    def test_the_longest_pause_wins(self):
        now = time.time()
        found = pause_among([_paused("harrowgate", now + 600, "a call"),
                             _paused("larch", now + 7200, "rendering")])
        assert found is not None
        assert (found.name, found.reason) == ("larch", "rendering")

    def test_a_pause_with_no_end_beats_a_timed_one(self):
        found = pause_among([_paused("larch", time.time() + 86400, "rendering"),
                             _paused("studio", None, "somebody is here")])
        assert found is not None and found.name == "studio"
        assert found.until == math.inf
        assert "until it is resumed" in found.said()

    def test_a_pause_that_has_run_out_is_not_adopted(self):
        assert pause_among([_paused("larch", time.time() - 60, "rendering")]) is None

    def test_a_machine_taking_work_carries_no_pause(self):
        assert pause_among([_paused("larch", None, "", paused=False)]) is None

    def test_a_machine_keeps_its_own_longer_pause(self):
        mine = Availability()
        mine.pause(minutes=600, reason="rendering here")
        assert adopt_pause(mine, pause_among([_paused("larch", time.time() + 600, "a call")])) is None
        assert mine.paused_reason == "rendering here"

    def test_a_pause_with_no_reason_still_says_why(self):
        mine = Availability()
        adopt_pause(mine, pause_among([_paused("larch", None)]))
        assert POOL_PAUSE in mine.may_start()[1]


class TestAdoptingThePauseOnStartup:
    def test_a_machine_that_starts_into_a_paused_pool_comes_up_paused(self, tmp_path, service_authority):
        larch = serve(tmp_path, tls.identity(tmp_path / "larch", "larch"), service_authority, name="larch")
        try:
            assert pause_fleet(Fanout(tmp_path / "root", minutes=120, reason="somebody is here"),
                               roster.members(port=free_port()))[0].ok
            mine = Availability()
            taken = adopt_pause(mine, member_pause(timeout_s=3.0, port=free_port()))
            assert taken is not None, "no member's pause was heard"
            assert not mine.may_start()[0]
            assert "somebody is here" in mine.may_start()[1]
        finally:
            stop(*larch)

    def test_a_machine_that_starts_into_a_running_pool_takes_work(self, tmp_path, service_authority):
        larch = serve(tmp_path, tls.identity(tmp_path / "larch", "larch"), service_authority, name="larch")
        try:
            mine = Availability()
            assert adopt_pause(mine, member_pause(timeout_s=3.0, port=free_port())) is None
            assert mine.may_start()[0]
        finally:
            stop(*larch)

    def test_a_machine_whose_node_does_not_answer_does_not_wait(self, monkeypatch, tmp_path):
        from poolhouse import node_health

        monkeypatch.setattr(node_health, "default_state", lambda: tmp_path / "no-node")  # a state directory no node runs in: the real refusal
        started = time.time()
        assert member_pause(timeout_s=30.0, port=free_port()) is None
        assert time.time() - started < 1.0


class TestThePauseTable:
    def test_a_paused_member_is_named_with_its_reason(self):
        schedule = Availability()
        schedule.pause(minutes=120, reason="somebody is here")
        row = roster.describe("larch", "https://10.0.0.4:8770", "machine", {"availability": schedule.public()})
        assert row["paused"] and "somebody is here" in row["paused_because"]
        text = table([row])
        assert "paused" in text

    def test_the_answers_name_this_machine_and_every_one_that_did_not_hear(self, tmp_path):
        answers = pause_fleet(Fanout(tmp_path, minutes=60), [])
        assert answers == []
        from poolhouse.fleet.pausing import Answer

        text = pause_table([Answer("studio", True, "paused until 12:00", True), Answer("larch", False, UNHEARD)], span="1h")
        assert text.startswith("paused 1 of 2 machines for 1h")
        assert "studio (this machine)" in text and UNHEARD in text and "1 machine did not hear this" in text


def test_a_pause_cannot_be_fanned_out_without_a_credential_for_the_row(tmp_path):
    answers = pause_fleet(Fanout(tmp_path, minutes=5),
                          [{"name": "stranger", "base_url": "https://10.0.0.9:8770"}])
    assert [(a.name, a.ok) for a in answers] == [("stranger", False)]
    assert "no credential" in answers[0].said


@pytest.mark.parametrize("span,minutes", [("2h 30m", 150), ("45s", 0.75), ("soon", None), ("", None)])
def test_the_length_of_the_pause_is_read_the_way_every_duration_is(span, minutes):
    from poolhouse.fleet.pausing import minutes_of

    assert minutes_of(span) == minutes

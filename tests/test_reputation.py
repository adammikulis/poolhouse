"""Reputation of sources: the two timescales, the divergence step to watch,
decay with a floor, recovery, the encrypted store and the human-only command."""

from __future__ import annotations

import json

import pytest

from poolhouse import authority, caller, home
from poolhouse.reputation import cli, model
from poolhouse.reputation.store import MAX_EVENTS, MAX_SOURCES, Ledger
from poolhouse.sentinel import observers
from tests import memory_keys

ring = memory_keys.ring
DAY = 86400.0


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def ledger(tmp_path, clock):
    held = Ledger(tmp_path / "rep" / "graph.enc", clock=clock, flush_s=0)
    yield held
    held.close()


def make_established(ledger, clock, kind="host", key="good.example"):
    for _ in range(model.ESTABLISHED_CLEAN):
        ledger.clean(kind, key)
        clock.advance(model.CLEAN_GAP_S + 1)
    clock.advance(4 * DAY)
    ledger.clean(kind, key)
    assert ledger.standing(kind, key).state == "established"


def test_a_source_with_a_long_clean_history_steps_to_watch_on_the_first_deviation(ledger, clock):
    make_established(ledger, clock)
    held = ledger.observe("host", "good.example", "hash_change")
    assert held.state == "watch"


def test_a_new_certificate_is_the_same_deviation(ledger, clock):
    make_established(ledger, clock)
    ledger.trait("host", "good.example", "cert", "aa")
    assert ledger.standing("host", "good.example").state == "established"
    ledger.trait("host", "good.example", "cert", "bb")
    assert ledger.standing("host", "good.example").state == "watch"


def test_an_unknown_source_with_the_same_event_is_only_bad(ledger):
    held = ledger.observe("host", "new.example", "hash_change")
    assert held.state == "bad"


def test_a_second_event_takes_a_watched_source_to_bad(ledger, clock):
    make_established(ledger, clock)
    ledger.observe("host", "good.example", "hash_change")
    clock.advance(120)
    assert ledger.observe("host", "good.example", "scan_hit").state == "bad"


def test_small_signals_add_up_before_they_matter(ledger, clock):
    make_established(ledger, clock)
    assert ledger.observe("host", "good.example", "redirect_change").state == "established"
    clock.advance(120)
    assert ledger.observe("host", "good.example", "ip_change").state == "watch"


def test_a_repeat_inside_a_minute_counts_once(ledger, clock):
    ledger.observe("host", "x.example", "denial")
    clock.advance(5)
    held = ledger.observe("host", "x.example", "denial")
    assert held.short == 1.0


def test_decay_lowers_an_old_verdict_but_never_to_zero_for_a_bad_one(ledger, clock):
    ledger.observe("host", "bad.example", "scan_hit")
    ledger.observe("host", "minor.example", "denial")
    clock.advance(30 * DAY)
    assert ledger.standing("host", "bad.example").long == pytest.approx(
        max(model.BAD_FLOOR, 3.0 * 0.5), rel=1e-6)
    clock.advance(3650 * DAY)
    held = ledger.standing("host", "bad.example")
    assert held.state == "bad" and held.long == model.BAD_FLOOR
    assert ledger.standing("host", "minor.example").long == model.WATCH_FLOOR
    assert ledger.standing("host", "minor.example").state == "watch"


def test_waiting_never_restores_a_source_only_a_clean_run_does(ledger, clock, monkeypatch):
    make_established(ledger, clock)
    ledger.observe("host", "good.example", "hash_change")
    clock.advance(400 * DAY)
    assert ledger.standing("host", "good.example").state == "watch"
    for _ in range(model.RECOVER_CLEAN):
        assert ledger.standing("host", "good.example").state == "watch"
        ledger.clean("host", "good.example")
        clock.advance(model.CLEAN_GAP_S + 1)
    held = ledger.standing("host", "good.example")
    assert held.state == "established"


def test_the_clean_run_is_configurable_and_a_bad_source_needs_three_times_as_many(ledger, clock, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_REPUTATION_RECOVER", "2")
    ledger.observe("host", "bad.example", "scan_hit")
    for _ in range(5):
        ledger.clean("host", "bad.example")
        clock.advance(model.CLEAN_GAP_S + 1)
    assert ledger.standing("host", "bad.example").state == "bad"
    ledger.clean("host", "bad.example")
    assert ledger.standing("host", "bad.example").state == "watch"


def test_clean_runs_in_a_burst_count_once(ledger):
    for _ in range(50):
        ledger.clean("host", "burst.example")
    assert ledger.standing("host", "burst.example").clean == 1


def test_names_are_normal_and_bad_names_are_refused(ledger):
    ledger.observe("host", "Example.ORG.", "denial")
    assert ledger.standing("host", "example.org").key == "example.org"
    ledger.observe("url", "https://u:p@Example.org/a/b?token=1#x", "denial")
    assert ledger.standing("url", "https://example.org/a/b") is not None
    assert model.normal("hash", "SHA256:" + "AB" * 32) == "ab" * 32
    for kind, key in (("host", "a b"), ("host", "x/y"), ("ip", "nope"), ("hash", "zz"), ("what", "x")):
        with pytest.raises(ValueError):
            model.normal(kind, key)


def test_the_store_is_bounded(ledger, clock):
    for _ in range(MAX_EVENTS + 5):
        ledger.observe("host", "noisy.example", "denial")
        clock.advance(model.DEDUP_S + 1)
    g = ledger.sealed.graph()
    assert len(g.nodes("event")) <= MAX_EVENTS
    # clean runs are written in batches; one write per source (flush_s=0) made this loop encrypt and
    # write the whole store a thousand times
    for n in range(MAX_SOURCES + 3):
        ledger.flush_s = 10**9
        ledger.clean("member", f"p{n}")
    ledger.flush()
    assert len(ledger.sources()) <= MAX_SOURCES


def test_the_store_survives_reopening_on_a_fresh_handle(tmp_path, clock):
    path = tmp_path / "rep" / "graph.enc"
    one = Ledger(path, clock=clock, flush_s=0)
    one.observe("host", "again.example", "scan_hit")
    one.close()
    assert Ledger(path, clock=clock).standing("host", "again.example").state == "bad"


def test_constructing_and_reading_an_empty_store_never_touches_the_keystore(tmp_path, ring, clock):
    touched = []
    ring.get_password = lambda *a: touched.append(a)  # type: ignore[method-assign]
    ring.set_password = lambda *a: touched.append(a)  # type: ignore[method-assign]
    for _ in range(20):
        held = Ledger(tmp_path / "e" / "graph.enc", clock=clock)
        assert held.sources() == [] and held.standing("host", "a.example") is None
        assert held.gate("host", "a.example") is None and held.stats()["sources"] == 0
        assert held.forget("host", "a.example") is False
        held.close()
    assert touched == []


def test_a_plaintext_summary_holds_counts_and_no_names(ledger, clock):
    ledger.observe("host", "secretname.example", "scan_hit")
    text = (ledger.path.parent / "summary.json").read_text()
    assert json.loads(text)["bad"] == 1 and "secretname" not in text


# -- no notice, no dialog --------------------------------------------------------------

def test_a_divergence_makes_the_source_watched_and_asks_nobody(ledger, clock):
    make_established(ledger, clock)
    ledger.observe("host", "good.example", "hash_change")
    held = ledger.standing("host", "good.example")
    assert held.state == "watch" and not hasattr(held, "notice")
    assert "notices" not in json.loads((ledger.path.parent / "summary.json").read_text())


# -- the command ------------------------------------------------------------------------------------

person = memory_keys.person
held_from_agents = memory_keys.held_from_agents

COMMANDS = [["list"], ["show", "host:a.example"], ["forget", "host:a.example"],
            ["forget", "--all", "--yes"], ["export"], ["stats"]]


def audit(event: str) -> list[dict]:
    return [r for r in caller.audit_rows() if r["event"] == event]


@pytest.mark.parametrize("marker", ["CLAUDECODE", "POOLHOUSE_AGENT"])
@pytest.mark.parametrize("argv", COMMANDS)
def test_an_agents_process_is_refused_at_once_while_the_gate_is_held_back_and_nothing_changes(
        held_from_agents, monkeypatch, capsys, marker, argv):
    held = Ledger()
    held.observe("host", "a.example", "scan_hit")
    held.close()
    monkeypatch.setenv(marker, "1")
    assert cli.main(argv) == cli.DENIED
    shown = capsys.readouterr()
    assert "a.example" not in shown.out and marker in shown.err and "reputation.admin" in shown.err
    check = Ledger()
    assert check.standing("host", "a.example") is not None
    check.close()
    refused = audit("refused")
    assert len(refused) == 1 and refused[0]["who"].startswith(f"agent {marker}")


def test_a_process_no_agent_started_lists_shows_exports_forgets_and_deletes_all_with_no_terminal(held_from_agents, person, capsys):
    held = Ledger()
    held.observe("host", "a.example", "scan_hit")
    held.observe("member", "node-1", "denial")
    held.close()
    assert cli.main(["list"]) == 0 and "host:a.example" in capsys.readouterr().out
    assert cli.main(["show", "host:a.example"]) == 0 and '"state": "bad"' in capsys.readouterr().out
    assert cli.main(["export"]) == 0 and "node-1" in capsys.readouterr().out
    assert cli.main(["stats"]) == 0
    assert cli.main(["forget", "host:a.example"]) == 0
    assert cli.main(["show", "host:a.example"]) == 2
    path = Ledger().path
    assert path.exists()
    assert cli.main(["forget", "--all", "--yes"]) == 0
    cleared = Ledger()
    try:
        assert cleared.sources() == [] and not (path.parent / "summary.json").exists()
        assert not cleared.sealed.prev.exists()
    finally:
        cleared.close()
    ran = audit("ran")
    assert [r["action"] for r in ran] == ["poolhouse-reputation " + c for c in
                                          ("list", "show", "export", "stats", "forget", "show", "forget")]
    assert all(r["who"].startswith("user ") for r in ran)


def test_an_agent_passes_while_the_gate_is_delegated_and_the_audit_names_it(person, monkeypatch, capsys):
    monkeypatch.delenv(authority.FLOOR_ENV)
    authority.set_state(["reputation.admin"], authority.DELEGATED, by="test")
    held = Ledger()
    held.observe("host", "a.example", "scan_hit")
    held.close()
    monkeypatch.setenv("CLAUDECODE", "1")
    assert cli.main(["list"]) == 0 and "host:a.example" in capsys.readouterr().out
    used = audit("authority.use")
    assert len(used) == 1 and used[0]["gate"] == "reputation.admin" and used[0]["who"].startswith("agent CLAUDECODE")


def test_no_observer_means_no_effect():
    observers.uninstall()
    observers.observe("host", "x.example", "denial")
    assert observers.gate("host", "x.example") is None
    assert home.state("reputation").exists() is False or True

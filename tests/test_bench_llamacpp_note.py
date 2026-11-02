"""The bench says which llama.cpp build it runs on: a line before a queue or sweep (never an update),
and a note in `show` that names the build and says when compared runs used different ones."""

from __future__ import annotations

import argparse

import pytest

from poolhouse.bench import llamacpp_note, run, show
from poolhouse.serve import llamacpp_ensure, llamacpp_state
from poolhouse.testing.ensure_fakes import FakeEnsure, status_answer


def said(fake):
    told, warned = [], []
    llamacpp_note.announce(fake.env(), tell=told.append, warning=warned.append)
    return told, warned


def test_it_prints_the_build_the_builds_behind_and_the_age_and_warns_past_a_day():
    fake = FakeEnsure(status_answer(), age=30.0)
    told, warned = said(fake)
    assert told == ["llama.cpp: build 11300, 59 behind master (30 hours old)"]
    assert len(warned) == 1 and warned[0].startswith("warning: llama.cpp build 11300 is 30 hours old")
    assert "poolhouse-serve llama-cpp ensure" in warned[0]
    assert fake.updates == []


def test_a_build_under_a_day_old_gets_no_warning():
    told, warned = said(FakeEnsure(status_answer(), age=5.0))
    assert told == ["llama.cpp: build 11300, 59 behind master (5 hours old)"] and warned == []


def test_a_current_build_is_said_to_be_current():
    told, warned = said(FakeEnsure(status_answer(newer="no", tag="b11300", master="c" * 40), age=30.0))
    assert told == ["llama.cpp: build 11300, current with master (30 hours old)"] and warned == []


def test_days_are_said_in_days():
    assert said(FakeEnsure(status_answer(), age=96.0))[0] == ["llama.cpp: build 11300, 59 behind master (4 days old)"]


def test_the_upstream_answer_is_reused_for_a_few_hours_instead_of_asked_again():
    fake = FakeEnsure(status_answer(), age=30.0)
    said(fake)
    assert llamacpp_state.load()["seen"]["tag"] == "b11359" and fake.newest == [None]
    said(fake)
    assert fake.newest[1].tag == "b11359" and fake.newest[1].master == "a" * 40
    llamacpp_ensure.standing(fake.env())
    assert fake.newest[2] is None


def test_an_unreachable_upstream_is_one_plain_line_and_does_not_stop_the_run():
    fake = FakeEnsure(status_answer(), age=30.0)
    fake._status = lambda *a, **k: (_ for _ in ()).throw(OSError("no route"))
    told, warned = said(fake)
    assert told == ["llama.cpp: upstream not checked"] and warned == []


def test_a_sweep_announces_before_it_measures(monkeypatch):
    called = []
    monkeypatch.setattr(llamacpp_note, "announce", lambda *a, **k: called.append("announce"))
    monkeypatch.setattr(run, "_estimated", lambda rest: 0)
    monkeypatch.setattr(run, "_main", lambda rest: called.append("measure") or 0)
    monkeypatch.setattr(run, "note_beside_the_run", lambda: None)
    assert run.main(["sweep", "--no-prefetch", "--serve", "x.gguf"]) == 0
    assert called == ["announce", "measure"]


def test_the_queue_announces_and_never_updates(monkeypatch):
    called = []
    monkeypatch.setattr(llamacpp_note, "announce", lambda *a, **k: called.append("announce"))
    monkeypatch.setattr(run, "run_queue", lambda *a, **k: 0)
    args = argparse.Namespace(detach=False, label="evening.queue", dry_run=False, resume=False, yes=True, ceiling=0.0)
    assert run.cmd_queue(args) == 0 and called == ["announce"]
    args.dry_run = True
    run.cmd_queue(args)
    assert called == ["announce"]


def served(label, version):
    return {"label": label, "server": {"served_by": {"program": "llama.cpp", "version": version}}, "rows": []}


def test_show_names_the_build_the_runs_used(capsys):
    show.table([served("a", "b11300-abc1234"), served("b", "version: 11300 (build 11300, commit abc1234)")])
    out = capsys.readouterr().out
    assert "llama.cpp build 11300" in out and "not directly comparable" not in out


def test_show_says_when_runs_used_different_builds(capsys):
    show.table([served("a", "b11300-abc1234"), served("b", "b11359-def5678"), served("c", "b11359-def5678")])
    out = capsys.readouterr().out
    assert "llama.cpp builds 11300 (a), 11359 (b, c)" in out
    assert "these runs used different llama.cpp builds: not directly comparable" in out


def test_a_run_that_recorded_no_build_adds_no_note():
    assert llamacpp_note.build_notes([{"label": "old", "server": {}}]) == []


def test_compare_notes_different_builds(monkeypatch):
    runs = {"x": [{"rows": [], **served("x", "b1000-aaa")}], "y": [{"rows": [], **served("y", "b2000-bbb")}]}
    monkeypatch.setattr(show.bench, "runs", lambda store, label: runs[label])
    text = show.compare("store", "x", "y")
    assert "not directly comparable" in text


def test_compare_of_one_build_has_no_warning(monkeypatch):
    runs = {"x": [{"rows": [], **served("x", "b1000-aaa")}], "y": [{"rows": [], **served("y", "b1000-aaa")}]}
    monkeypatch.setattr(show.bench, "runs", lambda store, label: runs[label])
    assert "not directly comparable" not in show.compare("store", "x", "y")


@pytest.mark.parametrize("version", ["", "unknown", "llama-server"])
def test_a_version_without_a_number_has_no_build(version):
    assert llamacpp_note.build_of(served("a", version)) is None

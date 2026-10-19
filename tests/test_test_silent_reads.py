"""Files the runner keeps that are there and unreadable are said so, and a slow run's overhead is recorded."""

from __future__ import annotations

import testhistory
import testjobs
import testphases


def test_a_missing_duration_history_is_empty_and_quiet(tmp_path, capsys):
    assert testhistory.load(tmp_path / "absent.json") == {}
    assert capsys.readouterr().err == ""


def test_a_corrupt_duration_history_is_empty_and_names_the_file_and_what_follows_on_stderr(tmp_path, capsys):
    path = tmp_path / "durations.json"
    path.write_text("{not json")
    assert testhistory.load(path) == {}
    err = capsys.readouterr().err
    assert str(path) in err and "start from nothing" in err


def test_a_job_file_that_is_missing_is_empty_and_quiet_and_a_corrupt_one_is_named(tmp_path, capsys):
    jobs = testjobs.Jobs(tmp_path)
    (tmp_path / "jobs" / "j1").mkdir(parents=True)
    assert jobs.read("j1") == {} and capsys.readouterr().err == ""
    (tmp_path / "jobs" / "j1" / "status.json").write_text("{not json")
    assert jobs.read("j1") == {}
    err = capsys.readouterr().err
    assert "job j1" in err and "status.json is unreadable" in err


def test_a_run_over_the_fixed_overhead_limit_is_recorded_with_its_seconds():
    slow = testphases.Phases(wall=40.0, collection=6.0, setup=1.0, tests=30.0, teardown=1.0)
    fast = testphases.Phases(wall=10.0, collection=1.0, setup=0.5, tests=8.0, teardown=0.5)
    assert testphases.record(slow) == {"fixed_s": 10.0, "over_limit": True}
    assert testphases.record(fast) == {"fixed_s": 2.0, "over_limit": False}

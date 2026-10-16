"""A run whose files stored no result says so in its report and its job status."""

# ruff: noqa: F811
from __future__ import annotations

import pytest
import testreuse_run as run
import testreuse_store as storage
from test_testreuse import FILE, project  # noqa: F401  (fixture)
from test_testreuse_review import attempt


def test_a_report_counts_the_files_that_stored_nothing_and_names_the_cause():
    report = run.Report(outcomes=[run.Outcome("a.py", unstored="a test was skipped"), run.Outcome("b.py", unstored="a test was skipped"),
                                  run.Outcome("c.py", unstored="mkdir /x"), run.Outcome("d.py", how="reused from 1")])
    assert report.degraded() == [("a test was skipped", 2), ("mkdir /x", 1)]
    last = report.lines()[-1]
    assert last == "test: reuse degraded: 3 of 4 file(s) stored no result (2 x a test was skipped; 1 x mkdir /x)"
    assert report.summary() == {"ran": 3, "reused": 1, "unstored": 3, "causes": [("a test was skipped", 2), ("mkdir /x", 1)]}


def test_a_report_of_files_that_all_stored_has_no_degraded_line():
    report = run.Report(outcomes=[run.Outcome("a.py", stored="id1"), run.Outcome("b.py", how="reused from 1")])
    assert not any("degraded" in line for line in report.lines()) and "unstored" not in report.summary()


@pytest.mark.slow
def test_a_passing_file_that_is_skipped_is_reported_as_degraded(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    (project / FILE).write_text("import pytest\n\n\ndef test_a():\n    pytest.skip('not here')\n")
    report, _ = attempt(project, store)
    assert "reuse degraded: 1 of 1 file(s) stored no result (1 x a test was skipped)" in report.lines()[-1]
    assert report.summary()["unstored"] == 1


@pytest.mark.slow
def test_a_file_that_stored_its_pass_is_not_degraded(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    report, _ = attempt(project, store)
    assert report.degraded() == [] and "degraded" not in report.lines()[-1]

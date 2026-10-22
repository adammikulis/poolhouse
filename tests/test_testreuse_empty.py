"""A run that executed none of the tests it was asked for exits non-zero and says why."""

# ruff: noqa: F811
from __future__ import annotations

import pytest
import testreuse_store as storage
from test_testreuse import FILE, project  # noqa: F401  (fixture)
from test_testreuse_review import attempt

pytestmark = pytest.mark.slow


def test_a_selector_that_leaves_every_test_out_exits_non_zero_with_the_reason(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    report, _ = attempt(project, store, extra=("-k", "matches_nothing"))
    assert report.status != 0
    assert any("no test ran" in line and FILE in line for line in report.lines())


def test_a_run_that_executed_a_test_is_not_a_run_of_nothing(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    report, _ = attempt(project, store)
    assert report.status == 0 and not any("no test ran" in line for line in report.lines())


def test_a_file_named_by_an_absolute_path_is_judged_by_the_tests_it_ran(project, tmp_path):
    """The junit file names a test file by its path from the checkout; a run that named the file by its absolute path ran its tests and was
    reported as a run of nothing (38 passed, exit 4)."""
    store = storage.Store(tmp_path / "store")
    report, launches = attempt(project, store, str(project / FILE))
    assert report.status == 0 and [o.file for o in report.outcomes] == [FILE]
    assert not any("no test ran" in line for line in report.lines())
    assert len(launches) == 1 and str(project / FILE) not in launches[0], "pytest is given the file once, by the path the junit file uses"
    again, launches = attempt(project, store, str(project / FILE))
    assert [o.how.split(" from ")[0] for o in again.outcomes] == ["reused"] and launches == [], "an earlier pass is found by either spelling"

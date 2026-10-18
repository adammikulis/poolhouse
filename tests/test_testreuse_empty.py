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

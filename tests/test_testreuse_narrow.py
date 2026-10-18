"""A run that selects part of a test file never leaves an entry that a run of the whole file would reuse."""

# ruff: noqa: F811
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import testreuse_narrow as narrow
import testreuse_run as run
import testreuse_store as storage
from test_testreuse import FILE, attempt, launcher, project  # noqa: F401  (fixture)

from poolhouse.activity import reuse

pytestmark = pytest.mark.slow

TWO = "def test_a():\n    assert True\n\n\ndef test_b():\n    assert True\n"
PYTEST = [sys.executable, "-m", "pytest", "-q"]


def stored(store: storage.Store) -> list[str]:
    return [row["kind"] for row in reuse.rows(store.folder)]


def test_a_node_run_leaves_no_entry_for_a_later_file_run_to_reuse(project, tmp_path):
    (project / FILE).write_text(TWO)
    store = storage.Store(tmp_path / "store")
    command = [*PYTEST, f"{FILE}::test_a"]
    session = run.Session(project, command, run.selectors(command, project), store, run.Events())
    launches: list = []
    first = session.run(launcher(project, launches), "treehash0001", False)
    assert first.status == 0 and len(launches) == 1
    assert stored(store) == []
    whole, launches = attempt(project, store)
    assert [o.how for o in whole.outcomes] == ["ran"] and len(launches) == 1
    assert stored(store) == ["pass"]


@pytest.mark.parametrize("extra", [("-k", "test_a"), ("--deselect", f"{FILE}::test_b"), ("-m", "not unmarked"),
                                   ("-k=test_a",), ("-ktest_a",)])
def test_a_run_that_selects_by_expression_leaves_no_entry_for_a_later_file_run(project, tmp_path, extra):
    (project / FILE).write_text(TWO)
    store = storage.Store(tmp_path / "store")
    first, _ = attempt(project, store, extra=extra, reuse_on=False)
    assert stored(store) == [], first.lines()
    again, launches = attempt(project, store, extra=extra)
    assert [o.how for o in again.outcomes] == ["ran"] and len(launches) == 1
    whole, launches = attempt(project, store)
    assert [o.how for o in whole.outcomes] == ["ran"] and len(launches) == 1
    assert stored(store) == ["pass"]


def test_a_narrowed_run_says_why_it_stored_nothing(project, tmp_path):
    (project / FILE).write_text(TWO)
    report, _ = attempt(project, storage.Store(tmp_path / "store"), extra=("-k", "test_a"))
    assert any("not stored: the run selects part of the file" in line for line in report.lines())


def test_a_session_knows_when_its_command_is_narrowed(project, tmp_path):
    store = storage.Store(tmp_path / "store")
    session = run.Session(project, [*PYTEST, "-k", "test_a", FILE], [FILE], store, run.Events())
    assert session.narrow
    assert not run.Session(project, [*PYTEST, FILE], [FILE], store, run.Events()).narrow


@pytest.mark.parametrize("words", [
    [f"{FILE}::test_a"], ["-k", "x", FILE], ["-kx", FILE], ["-xk", "x", FILE], ["-k=x", FILE],
    ["-m", "other", FILE], ["-mother", FILE], ["-vm", "other", FILE], ["--deselect", f"{FILE}::t", FILE],
    [f"--deselect={FILE}::t", FILE], ["--lf", FILE], ["--last-failed", FILE], ["--sw", FILE], ["--stepwise", FILE],
    ["--sw-skip", FILE], ["--sw-reset", FILE], ["--collect-only", FILE], ["--co", FILE], ["--setup-plan", FILE],
    ["--setup-only", FILE], ["-o", "python_functions=check", FILE], ["-opython_files=x.py", FILE],
    ["--override-ini=addopts=-k x", FILE], ["-c", "other.ini", FILE], ["--config-file=other.ini", FILE]])
def test_every_way_pytest_selects_part_of_a_file_is_narrowing(words):
    assert narrow.narrowed([*PYTEST, *words], {})


@pytest.mark.parametrize("words", [
    [FILE], ["-n", "3", FILE], ["-rfE", FILE], ["-m", "not slow and not heavy", FILE], ["-m", "slow", FILE],
    ["-mslow", FILE], ["--slow", FILE], ["-x", FILE], ["--maxfail=2", FILE], ["--ff", FILE], ["-p", "no:cacheprovider", FILE],
    ["-W", "error", FILE], ["--ignore=tests/other.py", FILE], ["-vv", "--tb=short", FILE], ["--junitxml=out.xml", FILE]])
def test_options_that_leave_every_test_of_a_file_in_are_not_narrowing(words):
    assert not narrow.narrowed([*PYTEST, *words], {})


@pytest.mark.parametrize("addopts", ["-k x", "--lf", "-m other", "--deselect a.py::t", "-k 'unterminated"])
def test_pytest_addopts_in_the_environment_narrows(addopts):
    assert narrow.narrowed([*PYTEST, FILE], {"PYTEST_ADDOPTS": addopts})
    assert not narrow.narrowed([*PYTEST, FILE], {"PYTEST_ADDOPTS": "-W error"})


def test_a_node_run_with_addopts_in_the_real_environment_is_narrowing(monkeypatch):
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k x")
    assert narrow.narrowed([*PYTEST, FILE])
    monkeypatch.delenv("PYTEST_ADDOPTS")
    assert not narrow.narrowed([*PYTEST, FILE])
    assert os.environ.get("PYTEST_ADDOPTS") is None


def test_the_tier_runs_pass_only_marks_the_narrowing_check_lets_through():
    from importlib import machinery, util

    root = Path(__file__).resolve().parents[1]
    loader = machinery.SourceFileLoader("tier_runner_narrow", str(root / "scripts" / "test"))
    module = util.module_from_spec(util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = module
    loader.exec_module(module)
    for flags in module.TIER_FLAGS.values():
        assert not narrow.narrowed([*PYTEST, *flags, FILE], {}), flags

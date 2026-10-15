"""Baseline matching requires exact failures and unchanged execution evidence."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import land_check
import land_failure_evidence as evidence
import land_run


def result(*identities, valid=True):
    return land_check.Result("affected", "fail", failed_files=["tests/test_case.py"],
                             receipt=evidence.Receipt(valid=valid, failures=list(identities)))


@pytest.mark.parametrize("new", [("tests/test_case.py::new", "failure", "other"),
                                  ("tests/test_case.py::old", "failure", "changed-error")])
def test_baseline_in_same_file_does_not_mask_new_failure(tmp_path, monkeypatch, new):
    from contextlib import nullcontext
    old = ("tests/test_case.py::old", "failure", "original")
    batch = SimpleNamespace(merged=[("bad", "sha")])
    check = land_check.Check("affected", "tier", ["python", "scripts/test", "all", "-n", "1", "tests/test_case.py"], ["tests/test_case.py"])
    monkeypatch.setattr(land_run, "probe_tree", lambda batch: nullcontext(tmp_path))
    monkeypatch.setattr(land_check, "probe_result", lambda *args: result(old))
    ejected = []
    monkeypatch.setattr(land_run, "eject", lambda *args: ejected.append(args[1]))
    assert land_run.blame(batch, check, result(old, new)) == "bad"
    assert ejected == ["bad"]


def test_matching_failures_are_baseline(tmp_path, monkeypatch):
    from contextlib import nullcontext
    old = ("tests/test_case.py::old", "error", "original")
    monkeypatch.setattr(land_run, "probe_tree", lambda batch: nullcontext(tmp_path))
    monkeypatch.setattr(land_check, "probe_result", lambda *args: result(old))
    check = land_check.Check("affected", "tier", ["python", "scripts/test", "all", "-n", "1", "tests/test_case.py"], ["tests/test_case.py"])
    assert land_run.blame(SimpleNamespace(), check, result(old)) == "baseline"


def test_unknown_failure_receipt_cannot_be_baseline(tmp_path, monkeypatch):
    from contextlib import nullcontext
    monkeypatch.setattr(land_run, "probe_tree", lambda batch: nullcontext(tmp_path))
    monkeypatch.setattr(land_check, "probe_result", lambda *args: result(valid=False))
    with pytest.raises(ValueError, match="cannot establish"):
        land_run.blame(SimpleNamespace(), land_check.Check("gate", "tier", []), result())


def test_bisection_compares_failure_difference(tmp_path, monkeypatch):
    old = ("tests/test_case.py::old", "failure", "original")
    new = ("tests/test_case.py::new", "failure", "new")
    outcomes = iter([result(old), result(old, new)])
    monkeypatch.setattr(land_check, "probe_result", lambda *args: next(outcomes))
    monkeypatch.setattr(land_run.lg, "git", lambda *args: None)
    batch = SimpleNamespace(root=tmp_path, merged=[("one", "1"), ("two", "2"), ("three", "3"), ("four", "4")])
    assert land_run.first_bad(batch, land_check.Check("affected", "tier", []), tmp_path, Counter([old])) == 3


def test_changed_tree_never_records_a_pass(tmp_path, monkeypatch):
    hashes = iter(["before", "after"])
    monkeypatch.setattr(land_check, "tree_hash", lambda root: next(hashes))
    monkeypatch.setattr(land_check, "_prior", lambda *args: None)
    monkeypatch.setattr(land_check, "run_command", lambda *args: (0, ""))
    monkeypatch.setattr(land_check, "_remember", lambda *args: pytest.fail("changed-tree pass remembered"))
    got = land_check.execute(tmp_path, land_check.Check("probe", "tier", ["python", "-c", "pass"]), "before")
    assert got.status == "fail" and not got.receipt.valid


@pytest.mark.parametrize("content", ["<!DOCTYPE x><testsuites/>", "<testsuite tests='2' failures='0' errors='0'><testcase classname='tests.case' name='x'/></testsuite>"])
def test_invalid_or_incomplete_junit_refuses(tmp_path, content):
    (tmp_path / "junit-fixture.xml").write_text(content)
    with pytest.raises(ValueError):
        evidence.failures(tmp_path, tmp_path)

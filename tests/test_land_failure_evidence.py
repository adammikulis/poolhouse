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


@pytest.fixture
def captured_job(tmp_path, monkeypatch):
    import subprocess
    import time

    import psutil
    import testjobs
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "tests/test_case.py").write_text("def test_old(): pass\n")
    store = tmp_path / "store"
    jobs = testjobs.Jobs(store / "fixture")
    options = {"code": 1, "owner": "agent-fixture", "wait_error": False}
    canceled = []
    def submit(argv, owner, checkout, script, extra):
        assert extra == {"task": "", "exclusive": False}
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(.1)"])
        started = psutil.Process(child.pid).create_time()
        child.wait(timeout=3)
        job = "fixture-captured"
        jobs.write(job, "spec.json", {"id": job, "root": str(root), "argv": argv,
                   "owner": {"id": options["owner"]}, "created": time.time()})
        jobs.write(job, "status.json", {"state": "done", "pid": child.pid, "started": started,
                   "finished": time.time(), "exit": options["code"]})
        (jobs.path(job) / "log").write_text("FAILED tests/test_case.py::old - error")
        (jobs.path(job) / "junit-one.xml").write_text("<testsuite tests='1' failures='1' errors='0' skipped='0'><testcase classname='tests.test_case' name='old'><failure message='old'>old</failure></testcase></testsuite>")
        return job
    monkeypatch.setattr(jobs, "submit", submit)
    def wait(*args, **kwargs):
        if options["wait_error"]:
            raise OSError("waiting failed")
        return jobs.read("fixture-captured")
    monkeypatch.setattr(jobs, "wait", wait)
    monkeypatch.setattr(jobs, "cancel", lambda *args: canceled.append(args))
    monkeypatch.setattr(evidence.testjobs, "Jobs", lambda *args: jobs)
    monkeypatch.setattr(evidence.testruns, "scope", lambda root: "fixture")
    monkeypatch.setattr(evidence.reuse, "reuse_base", lambda: store)
    monkeypatch.setattr(evidence, "tree_hash", lambda root: "fixed-tree")
    monkeypatch.setattr(evidence, "actor", lambda root: ({"id": "agent-fixture"}, True))
    return root, options, canceled


def test_captured_api_job_produces_bound_failure_evidence(captured_job):
    root, _, canceled = captured_job
    code, _, receipt = evidence.run(root, [sys.executable, "scripts/test", "all", "tests/test_case.py"])
    assert code == 1 and receipt.valid and receipt.job == "fixture-captured"
    assert receipt.failures[0][0] == "tests/test_case.py::old"
    assert not canceled


@pytest.mark.parametrize("code", [2, 3, 4, 5])
def test_invalid_runner_exit_is_not_baseline_evidence(captured_job, code):
    root, options, _ = captured_job
    options["code"] = code
    _, _, receipt = evidence.run(root, [sys.executable, "scripts/test", "all", "tests/test_case.py"])
    assert not receipt.valid and not receipt.complete


@pytest.mark.parametrize("failure", ["owner", "wait"])
def test_invalid_spec_or_wait_error_cancels_only_captured_job(captured_job, failure):
    root, options, canceled = captured_job
    if failure == "owner":
        options["owner"] = "different-agent"
    else:
        options["wait_error"] = True
    with pytest.raises((ValueError, OSError)):
        evidence.run(root, [sys.executable, "scripts/test", "all", "tests/test_case.py"])
    assert canceled and canceled[0][:2] == ("fixture-captured", "agent-fixture")


def test_anonymous_failure_does_not_authorize_baseline(captured_job, monkeypatch):
    root, options, _ = captured_job
    options["owner"] = ""
    monkeypatch.setattr(evidence, "actor", lambda root: ({"id": ""}, False))
    _, _, receipt = evidence.run(root, [sys.executable, "scripts/test", "all", "tests/test_case.py"])
    assert receipt.complete and not receipt.valid


def test_removed_existing_selector_is_not_a_passing_probe(tmp_path):
    got = land_check.probe_result(tmp_path, land_check.Check("affected", "tier", [], ["tests/removed.py"]))
    assert got.status == "fail" and "missing" in got.detail


def test_invalid_authenticated_receipt_never_records_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(land_check, "tree_hash", lambda root: "same")
    monkeypatch.setattr(land_check, "_prior", lambda *args: None)
    monkeypatch.setattr(evidence, "maintained", lambda *args: True)
    monkeypatch.setattr(evidence, "run", lambda *args: (0, "", evidence.Receipt(before="same", after="same", complete=True)))
    monkeypatch.setattr(land_check, "_remember", lambda *args: pytest.fail("invalid pass recorded"))
    assert land_check.execute(tmp_path, land_check.Check("affected", "tier", []), "same").status == "fail"


def test_failure_file_selection_uses_complete_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(land_check, "tree_hash", lambda root: "same")
    monkeypatch.setattr(land_check, "_prior", lambda *args: None)
    monkeypatch.setattr(evidence, "maintained", lambda *args: True)
    receipt = evidence.Receipt(before="same", after="same", valid=True, complete=True, failures=[("tests/earlier.py::case", "failure", "hash")])
    monkeypatch.setattr(evidence, "run", lambda *args: (1, "truncated log", receipt))
    assert land_check.execute(tmp_path, land_check.Check("affected", "tier", []), "same").failed_files == ["tests/earlier.py"]


@pytest.mark.parametrize("field,value", [("pid", True), ("started", float("nan")), ("finished", float("inf"))])
def test_nonfinite_or_boolean_job_identity_is_rejected(captured_job, monkeypatch, field, value):
    root, _, _ = captured_job
    original = evidence.land_live_jobs.record
    def read(path):
        data = original(path)
        if path.name == "status.json":
            data[field] = value
        return data
    monkeypatch.setattr(evidence.land_live_jobs, "record", read)
    if field in ("pid", "started"):
        with pytest.raises(ValueError):
            evidence.run(root, [sys.executable, "scripts/test", "all", "tests/test_case.py"])
    else:
        with pytest.raises(ValueError, match="terminal job receipt changed"):
            evidence.run(root, [sys.executable, "scripts/test", "all", "tests/test_case.py"])


def test_nonexistent_junit_case_cannot_supply_failure_evidence(tmp_path):
    (tmp_path / "junit-fixture.xml").write_text("<testsuite tests='1' failures='1' errors='0'><testcase classname='tests.missing' name='case'><failure>bad</failure></testcase></testsuite>")
    with pytest.raises(ValueError, match="existing checkout"):
        evidence.failures(tmp_path, tmp_path)


def test_composite_gate_failure_cannot_be_baseline_from_junit(captured_job):
    root, _, _ = captured_job
    code, _, receipt = evidence.run(root, [sys.executable, "scripts/test", "gate"])
    assert code == 1 and not receipt.valid and not receipt.complete

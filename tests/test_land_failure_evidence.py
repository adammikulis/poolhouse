"""Baseline matching requires exact failures and unchanged execution evidence."""
from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
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


@pytest.fixture
def collection_report(tmp_path):
    root, folder = tmp_path / "checkout", tmp_path / "reports"
    (root / "tests").mkdir(parents=True)
    folder.mkdir()
    (root / "tests/__init__.py").write_text("")
    (root / "tests/test_skipped.py").write_text(
        "import pytest\npytest.skip('optional dependency', allow_module_level=True)\n")
    (root / "tests/test_case.py").write_text("def test_old():\n    assert False, 'fixture failure'\n")
    return root, folder


def collection_case(root):
    case = ET.Element("testcase", classname="", name="tests.test_skipped", time="0.000")
    skipped = ET.SubElement(case, "skipped", message="collection skipped")
    skipped.text = repr((str(root / "tests/test_skipped.py"), 2, "Skipped: optional dependency"))
    return case


def collection_xml(folder, *cases):
    counts = Counter(child.tag for case in cases for child in case)
    suite = ET.Element("testsuite", tests=str(len(cases)), failures=str(counts["failure"]),
                       errors=str(counts["error"]), skipped=str(counts["skipped"]))
    suite.extend(cases)
    ET.ElementTree(suite).write(folder / "junit-fixture.xml", encoding="unicode")


def test_genuine_pytest_collection_skip_preserves_the_actual_failure(collection_report):
    root, folder = collection_report
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", f"--junitxml={folder / 'junit-generated.xml'}", "tests"],
        cwd=root, capture_output=True, text=True,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
    assert done.returncode == 1, done.stdout + done.stderr
    document, _ = evidence.read_xml(folder / "junit-generated.xml", 8 * 1024 * 1024)
    assert any(case.get("classname") == "" and case.find("skipped") is not None
               for case in document.iter("testcase"))
    found = evidence.failures(folder, root, ("tests/test_skipped.py", "tests/test_case.py"))
    assert len(found) == 1 and found[0][:2] == ("tests/test_case.py::test_old", "failure")


def test_identical_collection_skip_replicas_remain_skips(collection_report):
    root, folder = collection_report
    collection_xml(folder, collection_case(root), collection_case(root))
    assert evidence.failures(folder, root, ("tests/test_skipped.py",)) == []


@pytest.mark.parametrize("change", ["classname", "name", "missing-name", "pass", "error", "mixed", "duplicate-skip",
                                   "message", "nested", "literal", "outside", "unknown", "line-bool", "line-zero",
                                   "reason", "oversized"])
def test_malformed_collection_skip_cannot_supply_evidence(collection_report, change):
    root, folder = collection_report
    case = collection_case(root)
    skipped = case[0]
    if change == "classname":
        del case.attrib["classname"]
    elif change == "name":
        case.set("name", "tests/../test_skipped")
    elif change == "missing-name":
        del case.attrib["name"]
    elif change == "pass":
        case.remove(skipped)
    elif change == "error":
        case.remove(skipped)
        ET.SubElement(case, "error", message="collection failed").text = "broken"
    elif change == "mixed":
        ET.SubElement(case, "failure").text = "broken"
    elif change == "duplicate-skip":
        ET.SubElement(case, "skipped", message="collection skipped").text = skipped.text
    elif change == "message":
        skipped.set("message", "runtime skip")
    elif change == "nested":
        ET.SubElement(skipped, "failure").text = "broken"
    elif change == "literal":
        skipped.text = "__import__('os').system('false')"
    elif change == "outside":
        outside = root.parent / "outside.py"
        outside.write_text("pass\n")
        skipped.text = repr((str(outside), 1, "Skipped: optional dependency"))
    elif change == "unknown":
        case.set("name", "tests.test_unknown")
        skipped.text = repr((str(root / "tests/test_unknown.py"), 1, "Skipped: optional dependency"))
    elif change == "line-bool":
        skipped.text = repr((str(root / "tests/test_skipped.py"), True, "Skipped: optional dependency"))
    elif change == "line-zero":
        skipped.text = repr((str(root / "tests/test_skipped.py"), 0, "Skipped: optional dependency"))
    elif change == "reason":
        skipped.text = repr((str(root / "tests/test_skipped.py"), 2, "Passed: optional dependency"))
    elif change == "oversized":
        skipped.text = " " * 8193
    collection_xml(folder, case)
    with pytest.raises(ValueError):
        evidence.failures(folder, root)


@pytest.mark.parametrize("selectors", [("tests/test_case.py",), ("tests/test_skipped.py::test_unknown",)])
def test_collection_skip_stays_within_module_selector_scope(collection_report, selectors):
    root, folder = collection_report
    collection_xml(folder, collection_case(root))
    with pytest.raises(ValueError, match="selectors"):
        evidence.failures(folder, root, selectors)


def test_divergent_collection_skip_replicas_are_refused(collection_report):
    root, folder = collection_report
    other = collection_case(root)
    other[0].text = repr((str(root / "tests/test_skipped.py"), 2, "Skipped: a different dependency"))
    collection_xml(folder, collection_case(root), other)
    with pytest.raises(ValueError):
        evidence.failures(folder, root)


def test_regular_case_duplicates_are_still_refused(collection_report):
    root, folder = collection_report
    case = ET.Element("testcase", classname="tests.test_case", name="test_old")
    collection_xml(folder, case, case)
    with pytest.raises(ValueError, match="duplicate"):
        evidence.failures(folder, root)


@pytest.mark.parametrize("reverse", [False, True])
def test_a_collection_skip_cannot_mix_with_tests_from_its_module(collection_report, reverse):
    root, folder = collection_report
    cases = [collection_case(root), ET.Element("testcase", classname="tests.test_skipped", name="test_case")]
    collection_xml(folder, *reversed(cases) if reverse else cases)
    with pytest.raises(ValueError):
        evidence.failures(folder, root)



def test_a_collection_skip_cannot_resolve_outside_the_checkout(collection_report):
    root, folder = collection_report
    outside = root.parent / "outside.py"
    outside.write_text("pass\n")
    (root / "tests/test_skipped.py").unlink()
    (root / "tests/test_skipped.py").symlink_to(outside)
    collection_xml(folder, collection_case(root))
    with pytest.raises(ValueError):
        evidence.failures(folder, root)


def test_a_failure_free_skip_report_cannot_complete_an_exit_one_receipt(captured_job):
    root, _, _ = captured_job
    (root / "tests/test_skipped.py").write_text(
        "import pytest\npytest.skip('optional dependency', allow_module_level=True)\n")
    jobs = evidence.testjobs.Jobs(evidence.reuse.reuse_base() / evidence.testruns.scope(root))
    original = jobs.submit
    def submit(*args):
        job = original(*args)
        (jobs.path(job) / "junit-one.xml").unlink()
        collection_xml(jobs.path(job), collection_case(root))
        return job
    jobs.submit = submit
    code, _, receipt = evidence.run(root, [sys.executable, "scripts/test", "all", "tests/test_skipped.py"])
    assert code == 1 and receipt.failures == []
    assert not receipt.valid and not receipt.complete



def phase_case(outcome="failure", message="fixture call failure"):
    case = ET.Element("testcase", classname="tests.test_case", name="test_old", time="0.000")
    if outcome:
        ET.SubElement(case, outcome, message=message).text = message
    return case


def test_genuine_pytest_call_and_teardown_failures_both_survive(collection_report):
    root, folder = collection_report
    (root / "tests/test_case.py").write_text("""import pytest
@pytest.fixture
def broken_after():
    yield
    raise RuntimeError('fixture teardown')
def test_old(broken_after):
    assert False, 'fixture call'
""")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", f"--junitxml={folder / 'junit-generated.xml'}", "tests/test_case.py"],
        cwd=root, capture_output=True, text=True,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": ""})
    assert done.returncode == 1, done.stdout + done.stderr
    document, _ = evidence.read_xml(folder / "junit-generated.xml", 8 * 1024 * 1024)
    cases = list(document.iter("testcase"))
    assert [[child.tag for child in case] for case in cases] == [["failure"], ["error"]]
    found = evidence.failures(folder, root, ("tests/test_case.py",))
    assert len(found) == 2
    assert {identity for identity, _, _ in found} == {"tests/test_case.py::test_old"}
    assert {kind for _, kind, _ in found} == {"failure", "error"}
    assert len({digest for _, _, digest in found}) == 2


@pytest.mark.parametrize("change", ["same-failure", "same-error", "pass-first", "pass-second", "setup-error", "bad-envelope",
                                   "reverse", "third", "mixed-skip", "multiple-error", "nested", "cross-suite", "cross-file"])
def test_only_the_same_suite_ordered_call_teardown_sequence_is_allowed(collection_report, change):
    root, folder = collection_report
    call = phase_case()
    teardown = phase_case("error", 'failed on teardown with "fixture teardown"')
    cases = [call, teardown]
    if change == "same-failure":
        cases = [call, phase_case()]
    elif change == "same-error":
        cases = [teardown, phase_case("error", 'failed on teardown with "other teardown"')]
    elif change == "pass-first":
        cases[0] = phase_case("")
    elif change == "pass-second":
        cases[1] = phase_case("")
    elif change == "setup-error":
        teardown[0].set("message", 'failed on setup with "fixture setup"')
    elif change == "bad-envelope":
        teardown[0].set("message", "failed on teardown with fixture teardown")
    elif change == "reverse":
        cases.reverse()
    elif change == "third":
        cases.append(phase_case("error", 'failed on teardown with "third teardown"'))
    elif change == "mixed-skip":
        ET.SubElement(call, "skipped", message="runtime skip")
    elif change == "multiple-error":
        ET.SubElement(teardown, "error", message='failed on teardown with "other teardown"')
    elif change == "nested":
        ET.SubElement(teardown[0], "error").text = "nested"
    if change in {"cross-suite", "cross-file"}:
        collection_xml(folder, call)
        first = (folder / "junit-fixture.xml").read_text()
        collection_xml(folder, teardown)
        second = (folder / "junit-fixture.xml").read_text()
        if change == "cross-suite":
            (folder / "junit-fixture.xml").write_text("<testsuites>" + first + second + "</testsuites>")
        else:
            (folder / "junit-other.xml").write_text(first)
    else:
        collection_xml(folder, *cases)
    with pytest.raises(ValueError, match="duplicate"):
        evidence.failures(folder, root, ("tests/test_case.py",))


def test_multiple_error_children_in_one_testcase_remain_failures(collection_report):
    root, folder = collection_report
    case = phase_case("error", 'failed on setup with "fixture setup"')
    ET.SubElement(case, "error", message='failed on teardown with "fixture teardown"').text = "fixture teardown"
    collection_xml(folder, case)
    found = evidence.failures(folder, root, ("tests/test_case.py",))
    assert len(found) == 2 and all(kind == "error" for _, kind, _ in found)
    assert len({digest for _, _, digest in found}) == 2

"""Uninspectable reuse inputs refuse storage without erasing completed execution evidence."""
from __future__ import annotations

import errno
import sys

import pytest
import testreuse_key as keys
import testreuse_run as run
import testreuse_store as storage
from test_testreuse import FILE, Recorder, attempt, project as project_fixture
from test_testreuse_refusal import observed

from poolhouse.activity import reuse

pytestmark = pytest.mark.slow
project = project_fixture


@pytest.mark.parametrize("field", ["stats", "reads", "dirs"])
@pytest.mark.parametrize("failed", [0, 1])
def test_uninspectable_input_preserves_execution_but_never_stores(project, tmp_path, field, failed):
    store, events = storage.Store(tmp_path / "store"), Recorder()
    session = run.Session(project, [sys.executable, "-m", "pytest", FILE], [FILE], store, events)
    completions = []
    events.finished = lambda *args: completions.append(args)
    bad = "x" * 10000
    with pytest.raises(OSError) as caught:
        (project / bad).stat()
    assert caught.value.errno == errno.ENAMETOOLONG
    session.settle(FILE, run.Ran({"tests": 1, "failed": failed, "skipped": 0},
                                observed(**{field: {bad}}), session.command, True))
    outcome = session.outcome(FILE)
    assert outcome.passed is (not failed) and not outcome.stored
    assert completions == [(FILE, session.lookup(FILE).key, "fail" if failed else "pass-unstored", "")]
    assert reuse.rows(store.folder) == []
    assert len(outcome.detail) < 200 and bad not in outcome.detail


@pytest.mark.parametrize("phase", ["build_manifest", "manifest_holds"])
def test_inspection_oserror_refuses_storage_but_programming_errors_propagate(project, tmp_path, monkeypatch, phase):
    session = run.Session(project, [sys.executable, "-m", "pytest", FILE], [FILE], storage.Store(tmp_path / "store"))
    ran = run.Ran({"tests": 1, "failed": 0, "skipped": 0}, observed(), session.command, True)

    def inaccessible(*args, **kwargs):
        raise PermissionError(errno.EACCES, "untrusted-private-path")

    monkeypatch.setattr(keys, phase, inaccessible)
    session.settle(FILE, ran)
    assert session.outcome(FILE).passed and not session.outcome(FILE).stored
    assert "untrusted-private-path" not in session.outcome(FILE).detail

    def broken(*args, **kwargs):
        raise RuntimeError("implementation error")

    monkeypatch.setattr(keys, phase, broken)
    with pytest.raises(RuntimeError, match="implementation error"):
        session.settle(FILE, ran)


@pytest.mark.parametrize("failed", [False, True])
def test_real_pytest_oversized_probe_keeps_receipt_and_valid_sibling_cache(project, tmp_path, failed):
    (project / FILE).write_text('''from pathlib import Path
import pytest

def test_a():
    with pytest.raises(OSError):
        Path("x" * 10000).stat()
''' + ("    assert False, 'genuine execution failure'\n" if failed else ""))
    other = "tests/test_b.py"
    (project / other).write_text("def test_b():\n    assert 1 + 1 == 2\n")
    store = storage.Store(tmp_path / "store")
    report, launches = attempt(project, store, FILE, other)
    assert report.status == int(failed) and len(launches) == 1
    outcomes = {outcome.file: outcome for outcome in report.outcomes}
    assert outcomes[FILE].passed is (not failed) and not outcomes[FILE].stored
    if not failed:
        assert "result not stored" in outcomes[FILE].detail
    assert outcomes[other].passed and outcomes[other].stored
    entry = reuse.entry(store.folder, outcomes[other].stored)
    assert entry["counts"] == {"tests": 1, "failed": 0, "skipped": 0}
    assert entry["junit_sha256"]


def test_failed_canary_still_disables_reuse_before_manifest_work(project, tmp_path, monkeypatch):
    store = storage.Store(tmp_path / "store")
    session = run.Session(project, [sys.executable, "-m", "pytest", FILE], [FILE], store)
    disabled = []
    monkeypatch.setattr(store, "disable", lambda *args: disabled.append(args))

    def forbidden(*args, **kwargs):
        pytest.fail("a failed canary must not build a manifest")

    monkeypatch.setattr(keys, "build_manifest", forbidden)
    hit = storage.Hit("verified-pass", {"source": "existing verified entry"})
    session.settle(FILE, run.Ran({"tests": 1, "failed": 1, "skipped": 0},
                                observed(stats={"x" * 10000}), session.command, True, hit))
    assert not session.outcome(FILE).passed and session.report.status == 1
    assert disabled and disabled[0][1] == hit.entry


def test_directory_digest_only_calls_genuine_absence_missing(tmp_path, monkeypatch):
    assert keys.dir_sha(tmp_path / "absent") == "missing"
    regular = tmp_path / "regular"
    regular.write_text("data")
    assert keys.dir_sha(regular / "child") == "missing"

    def denied(path):
        raise PermissionError(errno.EACCES, "private-input")

    monkeypatch.setattr(type(tmp_path), "iterdir", denied)
    with pytest.raises(PermissionError):
        keys.dir_sha(tmp_path)


@pytest.mark.parametrize("error", [PermissionError(errno.EACCES, "private-input"),
                                  OSError(errno.ENAMETOOLONG, "x" * 10000)])
def test_old_directory_manifest_is_invalid_when_listing_is_uninspectable(project, monkeypatch, error):
    closures = keys.Closures(project)

    def denied(path):
        raise error

    monkeypatch.setattr(type(project), "iterdir", denied)
    manifest = {"files": {}, "dirs": {"src": "missing"}}
    reason = keys.manifest_holds(project, manifest, closures)
    assert reason == f"listing unavailable (errno {error.errno})"

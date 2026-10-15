"""Already-refused results avoid manifest work without changing storage eligibility."""

# ruff: noqa: F811
from __future__ import annotations

import sys

import pytest
import testreuse_key as keys
import testreuse_run as run
import testreuse_store as storage
from test_testreuse import FILE, Recorder, project  # noqa: F401 (fixture)

from poolhouse.activity import reuse

pytestmark = pytest.mark.slow


def observed(**changes):
    return {"reads": set(), "stats": set(), "dirs": set(), "links": {}, "marks": set(),
            "violations": set(), "skipped": False, "collected": 1, **changes}


@pytest.mark.parametrize("seen,count_changes,reason", [
    (None, {}, "no record of what it read"),
    (observed(violations={"z violation", "a violation"}, skipped=True, collected=2,
              marks={"gpu"}), {"skipped": 1}, "a violation"),
    (observed(skipped=True, collected=2), {}, "a test was skipped"),
    (observed(), {"skipped": 1}, "a test was skipped"),
    (observed(collected=2, marks={"gpu"}), {}, "1 of 2 collected tests ran"),
    (observed(marks={"model", "gpu"}), {}, "marked gpu"),
])
def test_refused_results_do_no_manifest_work(project, monkeypatch, seen, count_changes, reason):
    # This closure would also be refused as unobservable; the existing earlier reason must win.
    with (project / FILE).open("a") as stream:
        stream.write("\n# scandir\n")
    store, events = storage.Store(project.parent / "store"), Recorder()
    session = run.Session(project, [sys.executable, "-m", "pytest", FILE], [FILE], store, events)

    def forbidden_manifest(*args, **kwargs):
        pytest.fail("an already-refused result constructed a manifest")

    monkeypatch.setattr(keys, "build_manifest", forbidden_manifest)
    completions = []
    monkeypatch.setattr(events, "finished", lambda *args: completions.append(args))
    count = {"tests": 1, "failed": 0, "skipped": 0, **count_changes}
    session.settle(FILE, run.Ran(count, seen, session.command, True))
    outcome = session.outcome(FILE)
    assert outcome.passed and not outcome.stored
    assert outcome.detail == f"(result not stored: {reason})"
    assert completions == [(FILE, session.lookup(FILE).key, "pass-unstored", "")]
    assert reuse.rows(store.folder) == []


@pytest.mark.parametrize("mode", ["stored", "changed", "unobserved"])
def test_eligible_results_still_build_and_validate_the_manifest(project, tmp_path, monkeypatch, mode):
    if mode == "unobserved":
        with (project / FILE).open("a") as stream:
            stream.write("\n# scandir\n")
    store, events = storage.Store(tmp_path / "store"), Recorder()
    session = run.Session(project, [sys.executable, "-m", "pytest", FILE], [FILE], store, events)
    actual_build, actual_holds = keys.build_manifest, keys.manifest_holds
    calls = []

    def build(*args, **kwargs):
        calls.append("build")
        manifest = actual_build(*args, **kwargs)
        if mode == "changed":
            (project / "src/poolhouse/helper.py").write_text("VALUE = 2\n")
        return manifest

    def holds(*args, **kwargs):
        calls.append("holds")
        return actual_holds(*args, **kwargs)

    monkeypatch.setattr(keys, "build_manifest", build)
    monkeypatch.setattr(keys, "manifest_holds", holds)
    session.settle(FILE, run.Ran({"tests": 1, "failed": 0, "skipped": 0}, observed(), session.command, True))
    outcome = session.outcome(FILE)
    assert outcome.passed
    assert calls == (["build"] if mode == "unobserved" else ["build", "holds"])
    if mode == "stored":
        assert outcome.stored and outcome.detail == ""
        assert reuse.entry(store.folder, outcome.stored)["file"] == FILE
        assert events.seen == [("finished", FILE, "pass")]
    else:
        reason = ("result not stored: its closure uses scandir, which the runner cannot observe"
                  if mode == "unobserved" else "not stored: changed: src/poolhouse/helper.py")
        assert not outcome.stored and outcome.detail == f"({reason})"
        assert events.seen == [("finished", FILE, "pass-unstored")]
        assert reuse.rows(store.folder) == []

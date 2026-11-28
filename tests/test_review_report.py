"""scripts/review-report: rejects, review time, and the defects and reverts traced to a merge, from a real git repository and the
canned `gh` of tests/github_support.py."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from git_world import DEV, ROOT, Project, git
from github_support import serve

from poolhouse.workspace import merge_review

SCRIPT = ROOT / "scripts" / "review-report"


def round_of(verdict, at, attack=False, findings=()):
    record = {"v": 1, "verdict": verdict, "attack": attack, "findings": [{"severity": s, "text": t} for s, t in findings]}
    return {"createdAt": at, "body": merge_review.comment(record, "the note")}


def landed(project, number, files):
    """A branch of ``files`` merged into development as pull request ``number``: the merge commit and the commit it brought."""
    where = project.branch(f"work/{number}", files)
    brought = git(where, "rev-parse", "HEAD")
    git(project.root, "merge", "-q", "--no-ff", "-m", f"Merge pull request #{number}", f"work/{number}")
    return git(project.root, "rev-parse", "HEAD"), brought


@pytest.fixture
def history(tmp_path, monkeypatch):
    """Two merged pull requests, a fix of the first by its merge, a revert of the second and a trailer that names nothing."""
    (tmp_path / "git").mkdir()
    project = Project(tmp_path / "git")
    first, _ = landed(project, 1, {"docs/one.md": "one\n"})
    second, brought = landed(project, 2, {"docs/two.md": "two\n"})
    fix = project.advance("docs/one.md", "fixed\n")
    git(project.root, "commit", "-q", "--amend", "-m", f"fix: one\n\nFixes-merge: {first[:12]}\nFixes-merge: #99")
    fix = git(project.root, "rev-parse", "HEAD")
    git(project.root, "revert", "--no-edit", brought)
    revert = git(project.root, "rev-parse", "HEAD")
    git(project.root, "push", "-q", "-f", "origin", DEV)
    serve(tmp_path, monkeypatch, {})
    listed = [
        {"number": 1, "title": "one", "createdAt": "2026-01-01T00:00:00Z", "mergedAt": "2026-01-01T06:00:00Z",
         "mergeCommit": {"oid": first},
         "comments": [round_of("reject", "2026-01-01T01:00:00Z", attack=True, findings=[("high", "it breaks")]),
                      {"createdAt": "2026-01-01T02:00:00Z", "body": "a comment that is no review <!-- poolhouse-review {} -->"},
                      round_of("accept", "2026-01-01T04:00:00Z", findings=[("low", "a name could be clearer")])]},
        {"number": 2, "title": "two", "createdAt": "2026-01-02T00:00:00Z", "mergedAt": "2026-01-02T01:00:00Z",
         "mergeCommit": {"oid": second}, "comments": []}]
    (tmp_path / "replies" / "pr_list.json").write_text(json.dumps(listed))
    return project, fix, revert, tmp_path / "gh.log"


def report(project, *words):
    done = subprocess.run([sys.executable, str(SCRIPT), "--base", DEV, *words], cwd=project.root, capture_output=True, text=True,
                          timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_it_reports_rejects_review_time_defects_and_reverts_of_the_merged_changes(history):
    project, fix, revert, log = history
    found = json.loads(report(project, "--json"))
    assert (found["merged"], found["with_recorded_review"], found["rejects"], found["rejects_per_reviewed_change"]) == (2, 1, 1, 1.0)
    assert (found["defects_found_after_merge"], found["reverts"]) == (1, 1)
    assert (found["median_hours_to_first_review"], found["median_hours_to_accept"], found["median_hours_to_merge"]) == (1.0, 4.0, 3.5)
    one, two = found["changes"]
    assert one["defects"] == [fix[:12]] and one["reverts"] == [] and one["follow_ups"] == ["a name could be clearer"]
    assert (one["rounds"], one["attack_rounds"]) == (2, 1)
    assert two["reverts"] == [revert[:12]] and two["defects"] == []
    assert found["unresolved"] == [f"{fix[:12]} names #99, which is none of the merges read"]
    assert all(line.startswith("GET ") for line in log.read_text().splitlines()), "it writes nothing to GitHub"


def test_the_text_names_the_same_things(history):
    project, fix, revert, _ = history
    said = report(project)
    for shown in ("rejects per reviewed change: 1.0", "defects found after merge: 1", "reverts: 1", f"fixed after merge by {fix[:12]}",
                  f"reverted by {revert[:12]}", "follow-up: a name could be clearer"):
        assert shown in said

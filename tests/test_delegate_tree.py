"""A delegate that edits a git project works on a worktree and branch of its own, which the launcher makes, commits to and removes; the session
that started it is left the branch to review and is woken when it ends or asks.

PLUMBING ONLY: no model is served and no OpenCode runs. The board, the store and git are real. `converse` is replaced by a function that writes the
files a run would have published and says in every word it returns that it is `[CANNED RESPONSE, NOT A MODEL]`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from delegate_kit import agent_names, said, started

from poolhouse import (
    delegate,
    delegate_review,
    delegate_run,
    delegate_store as store,
    harness,
)
from poolhouse.isolation.workspace import Published

pytest_plugins = ["node_kit", "delegate_kit"]
CANNED = "[CANNED RESPONSE, NOT A MODEL]"


def git(folder, *words):
    return subprocess.run(["git", "-C", str(folder), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *words], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def leading(lead_node, detached):
    """A lead whose checkout holds a commit, an uncommitted draft and a commit hook that would leave a mark."""
    node, lead, project = lead_node
    project = project.resolve()
    git(project, "add", "-A")
    git(project, "commit", "--allow-empty", "-m", "start")
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{project.parent / 'hook-ran'}'\nexit 1\n")
    hook.chmod(0o755)
    (project / "draft.txt").write_text("the lead's draft\n")
    return node, lead, project


def checkout(project):
    """What of the lead's checkout a delegate must leave as it was."""
    return (git(project, "rev-parse", "HEAD"), git(project, "branch", "--show-current"), git(project, "status", "--porcelain"),
            (project / ".git" / "config").read_bytes())


def working(monkeypatch, *, accepted=True, fails=False):
    """A run that changes a file in its worktree, as a publication or a run with no sandbox would."""
    def converse(rec, _bridge, _mine):
        Path(rec.workdir, "made.py").write_text("x = 1\n")
        Path(rec.workdir, "unnamed.py").write_text("written, and in no publication\n")
        if fails:
            raise RuntimeError("PLUMBING: the run failed")
        return harness.Answer(text=f"{CANNED} done", spent=harness.Usage(turns=1), published=Published(applied=("made.py",) if accepted else ()),
                              review=delegate_review.Outcome(accepted, 1, 3))

    monkeypatch.setattr(delegate_run, "converse", converse)


def run_one(node, lead, project, **kwargs):
    assert started("--task", "add made.py", "--project", str(project), "--edit", checked=True, tree=True) == 0
    [name] = agent_names(node, lead)
    return name, store.load(name)


def test_an_accepted_change_is_committed_by_name_to_a_branch_left_for_the_lead_and_the_worktree_is_gone(leading, monkeypatch, capsys):
    node, lead, project = leading
    before = checkout(project)
    working(monkeypatch)
    name, rec = run_one(node, lead, project)
    assert rec.branch == f"delegate/{name}" and Path(rec.workdir) == project.parent / f"{project.name}-{name}" and Path(rec.workdir).is_dir()
    assert rec.source == str(project) and "1 uncommitted file(s)" in said(capsys)
    assert not (Path(rec.workdir) / "draft.txt").exists()
    assert delegate_run.run(name) == 0, store.result(name)
    row = store.result(name)
    sha = row["tree"]["commit"]
    assert sha == git(project, "rev-parse", rec.branch) and row["tree"]["branch"] == rec.branch, row
    assert git(project, "diff", "--name-only", f"{before[0]}..{rec.branch}") == "made.py", "named files only"
    assert git(project, "log", "-1", "--format=%an %ae", rec.branch) == f"{name} {name}@poolhouse.invalid"
    assert not Path(rec.workdir).exists() and rec.workdir not in git(project, "worktree", "list")
    assert checkout(project) == before and not (project / "made.py").exists() and not (project.parent / "hook-ran").exists()
    [told] = [e for e in node.session(lead).read(inbox=True).entries if e.sender == name]
    assert told.fields["subject"] == "delegate done (review waiting)"
    assert f"a review is waiting for you: branch {rec.branch} at {sha}" in told.text and "nothing was merged or pushed" in told.text, told.text
    assert delegate.main(["grade", name, "correct", "--critique", "reviewed the branch", "--check", "git show => one file"]) == 0
    assert [(each["kind"], each["grader"]) for each in store.graded(name)["grades"]] == [("grade", lead.name)]
    assert store.graded(name)["run"]["tree"]["commit"] == sha


@pytest.mark.parametrize("how", ["rejected", "failed", "stopped"])
def test_a_run_that_is_not_accepted_leaves_no_worktree_and_no_branch(leading, monkeypatch, how):
    node, lead, project = leading
    before = checkout(project)
    working(monkeypatch, accepted=False, fails=how == "failed")
    name, rec = run_one(node, lead, project)
    if how == "stopped":   # the runner never ran: `stop` clears what it left
        assert delegate.main(["stop", name]) == 0
    else:
        assert delegate_run.run(name) == 1
        row = store.result(name)
        assert row["state"] == how and row["tree"]["commit"] == "" and "worktree and branch are removed" in row["detail"], row
    assert not Path(rec.workdir).exists() and git(project, "branch", "--list", "delegate/*") == "" and checkout(project) == before


def test_in_place_and_a_delegate_that_only_reads_make_no_worktree(leading):
    node, lead, project = leading
    assert started("--task", "edit here", "--project", str(project), "--edit") == 0
    assert started("--task", "read", "--project", str(project), tree=True) == 0
    records = [store.load(name) for name in agent_names(node, lead)]
    assert [(each.branch, each.workdir == each.source) for each in records] == [("", True), ("", True)], records
    assert git(project, "branch", "--list", "delegate/*") == ""


def test_a_wait_returns_when_the_delegate_asks_and_again_when_it_ends(leading, monkeypatch, capsys):
    node, lead, project = leading
    working(monkeypatch)
    assert started("--task", "add made.py", "--project", str(project), "--edit", checked=True, tree=True) == 0
    [name] = agent_names(node, lead)
    store.set_status(name, "asking", "waiting for the lead to answer message 7")
    capsys.readouterr()
    assert delegate.main(["wait", name]) == delegate.ASKS
    assert "asks you something" in said(capsys)
    assert delegate_run.run(name) == 0
    capsys.readouterr()
    assert delegate.main(["wait", name]) == 0
    assert "a review is waiting" in said(capsys)

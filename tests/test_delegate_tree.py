"""A delegate that edits a git project works on a worktree and branch of its own, which the launcher makes, commits to and removes; the session
that started it is left the branch to review and is woken when it ends or asks.

PLUMBING ONLY: no model is served and no OpenCode runs. The board, the store and git are real. `converse` is replaced by a function that writes the
files a run would have published and says in every word it returns that it is `[CANNED RESPONSE, NOT A MODEL]`.
"""

from __future__ import annotations

import functools
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from delegate_kit import agent_names, said, started

from poolhouse import (
    delegate,
    delegate_review,
    delegate_run,
    delegate_store as store,
    delegate_tree,
    harness,
    jobs,
    lead_bridge,
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
        return harness.Answer(text=f"{CANNED} done", spent=harness.Usage(turns=1), published=Published(applied=("made.py",)),
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
def test_the_work_of_a_run_that_is_not_accepted_is_committed_to_its_branch_as_unfinished(leading, monkeypatch, capsys, how):
    node, lead, project = leading
    before = checkout(project)
    working(monkeypatch, accepted=False, fails=how == "failed")
    name, rec = run_one(node, lead, project)
    if how == "stopped":   # the runner was gone before it settled: `stop` commits what it left in the worktree
        Path(rec.workdir, "made.py").write_text("x = 1\n")
        assert delegate.main(["stop", name]) == 0
        told = said(capsys)
        assert "unfinished work is committed" in told, told
    else:
        assert delegate_run.run(name) == 1
        row = store.result(name)
        assert row["state"] == how and row["tree"]["commit"] == git(project, "rev-parse", rec.branch), row
        assert f"its unfinished work is committed, not accepted, on branch {rec.branch}" in row["detail"], row
    named = git(project, "diff", "--name-only", f"{before[0]}..{rec.branch}").split()
    # a run that left no word of what it published has all that differs in its worktree, which only it was published into
    assert named == (["made.py", "unnamed.py"] if how == "failed" else ["made.py"])
    message = git(project, "log", "-1", "--format=%B", rec.branch)
    assert f"UNFINISHED, the run ended {how}" in message, message
    assert not Path(rec.workdir).exists() and checkout(project) == before and not (project.parent / "hook-ran").exists()


def test_a_run_that_made_nothing_leaves_no_branch(leading):
    node, lead, project = leading
    name, rec = run_one(node, lead, project)
    assert delegate.main(["stop", name]) == 0
    assert not Path(rec.workdir).exists() and git(project, "branch", "--list", "delegate/*") == ""


def test_work_git_cannot_commit_is_left_in_its_worktree(leading, monkeypatch):
    node, lead, project = leading
    working(monkeypatch, accepted=False)
    name, rec = run_one(node, lead, project)
    held = Path(git(rec.workdir, "rev-parse", "--absolute-git-dir")) / "index.lock"
    held.write_text("PLUMBING: another git holds the index\n")
    assert delegate_run.run(name) == 1
    row = store.result(name)
    assert row["tree"]["commit"] == "" and f"left in the worktree {rec.workdir}" in row["detail"], row
    assert Path(rec.workdir, "made.py").read_text() == "x = 1\n" and git(project, "branch", "--list", rec.branch)


def lead_commits(tree, name):
    """A commit the lead makes of its own in the delegate's worktree, past the fixture's hook; its id."""
    Path(tree, name).write_text("LEAD = 1\n")
    git(tree, "add", name)
    git(tree, "-c", f"core.hooksPath={os.devnull}", "commit", "-qm", "the lead's own fix")
    return git(tree, "rev-parse", "HEAD")


def test_a_stop_leaves_the_branch_and_worktree_of_a_run_that_ended(leading, monkeypatch):
    """The run ended with a file git would not take, so its worktree stands, and the lead committed there since."""
    node, lead, project = leading
    (project / ".gitignore").write_text("*.log\n")
    git(project, "add", ".gitignore")
    git(project, "-c", f"core.hooksPath={os.devnull}", "commit", "-qm", "ignore logs")

    def converse(rec, _bridge, _mine):
        for each in ("made.py", "kept.log"):
            Path(rec.workdir, each).write_text("x = 1\n")
        return harness.Answer(text=f"{CANNED} done", spent=harness.Usage(turns=1), published=Published(applied=("kept.log", "made.py")),
                              review=delegate_review.Outcome(True, 1, 3))

    monkeypatch.setattr(delegate_run, "converse", converse)
    name, rec = run_one(node, lead, project)
    delegate_run.run(name)
    assert "git would not take" in store.result(name)["detail"] and Path(rec.workdir, "kept.log").exists(), store.result(name)
    mine = lead_commits(rec.workdir, "lead.py")
    assert delegate.main(["stop", name]) == 0
    assert git(project, "branch", "--list", rec.branch) and git(project, "rev-parse", rec.branch) == mine
    assert Path(rec.workdir, "kept.log").exists(), "the file git would not take is still there"


def test_a_branch_that_holds_a_commit_is_never_deleted(leading, capsys):
    node, lead, project = leading
    name, rec = run_one(node, lead, project)
    mine = lead_commits(rec.workdir, "lead.py")
    assert delegate.main(["stop", name]) == 0
    assert git(project, "branch", "--list", rec.branch) and git(project, "rev-parse", rec.branch) == mine
    assert not Path(rec.workdir).exists() and f"branch {rec.branch} is kept" in said(capsys)


def test_a_stop_settles_nothing_while_the_runner_is_still_ending(leading, monkeypatch, capsys):
    node, lead, project = leading
    name, rec = run_one(node, lead, project)
    Path(rec.workdir, "made.py").write_text("x = 1\n")
    code = "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint(flush=True)\ntime.sleep(60)"
    runner = subprocess.Popen([sys.executable, "-I", "-c", code], stdout=subprocess.PIPE)   # PLUMBING: a runner that is keeping what it made
    try:
        runner.stdout.readline()
        jobs.record(delegate.JOB, pid=runner.pid, home=store.folder(name), refuse_if_alive=False)
        monkeypatch.setattr(delegate.jobs, "stop", functools.partial(jobs.stop, wait=0.2))
        assert delegate.main(["stop", name]) == 1
        assert Path(rec.workdir, "made.py").exists() and git(project, "rev-parse", rec.branch) == rec.commit
        assert "is still running" in said(capsys) and store.status(name).get("state") != "stopped"
    finally:
        runner.kill()
        runner.wait()
        runner.stdout.close()


def test_a_file_made_and_removed_within_a_run_is_nothing_to_take_and_the_worktree_goes(leading, monkeypatch):
    node, lead, project = leading
    (project / "old.py").write_text("OLD = 1\n")
    git(project, "add", "old.py")
    git(project, "-c", f"core.hooksPath={os.devnull}", "commit", "-qm", "old")

    def converse(rec, _bridge, _mine):
        Path(rec.workdir, "made.py").write_text("x = 1\n")
        Path(rec.workdir, "old.py").unlink()
        return harness.Answer(text=f"{CANNED} done", spent=harness.Usage(turns=1),
                              published=Published(applied=("made.py", "old.py", "scratch.py")), review=delegate_review.Outcome(True, 1, 3))

    monkeypatch.setattr(delegate_run, "converse", converse)
    name, rec = run_one(node, lead, project)
    assert delegate_run.run(name) == 0, store.result(name)
    row = store.result(name)
    assert git(project, "show", "--name-status", "--format=", row["tree"]["commit"]).split() == ["A", "made.py", "D", "old.py"], row
    assert not Path(rec.workdir).exists() and "would not take" not in row["detail"], row


def test_a_stop_that_arrives_while_the_work_is_committed_cuts_nothing_short(leading, monkeypatch):
    node, lead, project = leading
    working(monkeypatch)
    name, rec = run_one(node, lead, project)
    commit = delegate_tree.committed

    def stopped_here(*given):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.1)
        return commit(*given)

    monkeypatch.setattr(delegate_tree, "committed", stopped_here)
    before = signal.getsignal(signal.SIGTERM)
    try:
        assert delegate_run.run(name) == 0
        assert signal.getsignal(signal.SIGTERM) == before, "the process hears a stop again once the run is over"
    finally:
        signal.signal(signal.SIGTERM, before)
        lead_bridge.STOPPED.clear()
    row = store.result(name)
    assert row["state"] == "done" and row["tree"]["commit"] == git(project, "rev-parse", rec.branch), row


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

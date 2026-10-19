"""scripts/primary_sync.py on real git repositories: a bare origin, a primary checkout on the development branch and a worktree that pushes."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import primary_sync
import pytest
from land_support import DEV, Project, git as run_git

from poolhouse import lock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "primary_sync.py"
FAST = ("--wait-s", "20", "--poll-s", "0.2")


class Rig:
    """A project whose primary checkout trails an origin that a sibling worktree pushes to."""

    def __init__(self, tmp_path: Path) -> None:
        self.proj = Project(tmp_path)
        self.primary = self.proj.root
        self.pusher = self.proj.branch("pusher", {})
        run_git(self.pusher, "config", "user.name", "Pusher")
        run_git(self.pusher, "config", "user.email", "pusher@example.invalid")
        self.count = 0

    def commit(self) -> str:
        """A new commit in the pusher's worktree, not pushed; its id."""
        self.count += 1
        self.proj.write(self.pusher, f"src/poolhouse/pushed{self.count}.py", f"VALUE = {self.count}\n")
        return self.proj.commit(self.pusher, f"chore: pushed {self.count}")

    def push(self) -> str:
        """Commit in the pusher's worktree and push it to the development branch; the commit's id."""
        sha = self.commit()
        self.send()
        return sha

    def send(self) -> None:
        """Push the pusher's branch to the development branch."""
        run_git(self.pusher, "push", "-q", "origin", f"HEAD:refs/heads/{DEV}")

    def head(self) -> str:
        """The commit the primary checkout is on."""
        return run_git(self.primary, "rev-parse", "HEAD")

    def sync(self, *extra: str) -> str:
        """Run the script in the primary checkout with a PATH that is the test's own; its output."""
        done = subprocess.run([sys.executable, str(SCRIPT), "--dev", DEV, *extra], cwd=self.primary, capture_output=True, text=True,
                              check=False, timeout=120)
        assert done.returncode == 0, done.stdout + done.stderr
        return done.stdout.strip()


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_a_clean_primary_follows_origin(rig):
    sha = rig.push()
    assert rig.head() != sha
    assert "moved from" in rig.sync()
    assert rig.head() == sha
    assert (rig.primary / "src/poolhouse/pushed1.py").read_text() == "VALUE = 1\n"


def test_a_second_run_changes_nothing(rig):
    sha = rig.push()
    rig.sync()
    assert "level with" in rig.sync()
    assert rig.head() == sha


@pytest.mark.parametrize("change", ["tracked", "untracked", "staged"])
def test_a_primary_with_changes_is_left_as_it_is(rig, change):
    before = rig.head()
    rig.push()
    if change == "tracked":
        (rig.primary / "docs/a.md").write_text("mine\n")
    elif change == "untracked":
        (rig.primary / "notes.txt").write_text("mine\n")
    else:
        (rig.primary / "docs/a.md").write_text("mine\n")
        run_git(rig.primary, "add", "docs/a.md")
    state = run_git(rig.primary, "status", "--porcelain")
    assert "the primary checkout has changes" in rig.sync()
    assert rig.head() == before and run_git(rig.primary, "status", "--porcelain") == state
    assert not run_git(rig.primary, "stash", "list")
    assert (rig.primary / ("notes.txt" if change == "untracked" else "docs/a.md")).read_text() == "mine\n"


def test_a_primary_on_another_branch_is_left_as_it_is(rig):
    run_git(rig.primary, "checkout", "-q", "-b", "elsewhere")
    before = rig.head()
    rig.push()
    assert f"is on elsewhere, not {DEV}" in rig.sync()
    assert rig.head() == before and run_git(rig.primary, "branch", "--show-current") == "elsewhere"
    assert run_git(rig.primary, "rev-parse", DEV) == before


def test_a_detached_primary_is_left_as_it_is(rig):
    run_git(rig.primary, "checkout", "-q", "--detach")
    before = rig.head()
    rig.push()
    assert "is on no branch" in rig.sync()
    assert rig.head() == before


def test_a_primary_that_has_diverged_is_left_as_it_is(rig):
    rig.proj.write(rig.primary, "src/poolhouse/mine.py", "VALUE = 'mine'\n")
    mine = rig.proj.commit(rig.primary, "chore: mine")
    rig.push()
    assert "have each moved on" in rig.sync()
    assert rig.head() == mine and not run_git(rig.primary, "status", "--porcelain")


def test_the_owners_branch_is_never_followed(rig):
    before = rig.head()
    assert "main is the owner's" in rig.sync("--dev", "main")
    assert rig.head() == before


def test_a_primary_ahead_of_origin_is_left_as_it_is(rig):
    rig.proj.write(rig.primary, "src/poolhouse/mine.py", "VALUE = 'mine'\n")
    mine = rig.proj.commit(rig.primary, "chore: mine")
    assert "ahead of it" in rig.sync()
    assert rig.head() == mine


def test_a_merge_under_way_is_left_as_it_is(rig):
    run_git(rig.primary, "checkout", "-q", "-b", "side")
    rig.proj.write(rig.primary, "docs/a.md", "side\n")
    rig.proj.commit(rig.primary, "chore: side")
    run_git(rig.primary, "checkout", "-q", DEV)
    rig.proj.write(rig.primary, "docs/a.md", "main\n")
    mine = rig.proj.commit(rig.primary, "chore: main")
    assert subprocess.run(["git", "-C", str(rig.primary), "merge", "-q", "side"], capture_output=True, check=False).returncode != 0
    rig.push()
    assert "left alone" in rig.sync()
    assert rig.head() == mine and (rig.primary / ".git" / "MERGE_HEAD").exists()


@pytest.mark.parametrize("marker", ["rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG"])
def test_an_operation_under_way_is_left_as_it_is(rig, marker):
    before = rig.head()
    rig.push()
    path = rig.primary / ".git" / marker
    path.mkdir() if marker.startswith("rebase") else path.write_text("x\n")
    assert f"under way ({marker})" in rig.sync()
    assert rig.head() == before


def test_runs_at_the_same_time_leave_the_primary_at_the_tip(rig):
    sha = rig.push()
    procs = [subprocess.Popen([sys.executable, str(SCRIPT), "--dev", DEV], cwd=rig.primary, stdout=subprocess.PIPE, text=True)
             for _ in range(5)]
    lines = [p.communicate(timeout=120)[0].strip() for p in procs]
    assert [p.returncode for p in procs] == [0] * 5
    assert rig.head() == sha and not run_git(rig.primary, "status", "--porcelain")
    said = [ln for out in lines for ln in out.splitlines()]
    assert sum("moved from" in ln for ln in said) == 1, said
    assert all(ln.startswith("primary-sync:") for ln in said)


def test_a_run_that_finds_the_lock_leaves_a_note_and_changes_nothing(rig):
    before = rig.head()
    rig.push()
    common = Path(run_git(rig.primary, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    with lock.only_one(common / "primary-sync.lock", wait=False):
        assert "another sync is running" in rig.sync()
    assert rig.head() == before and (common / "primary-sync.again").exists()


def test_the_run_holding_the_lock_goes_round_again_for_a_note_left_meanwhile(rig, monkeypatch):
    calls = []
    real = primary_sync.once

    def once(root, dev):
        calls.append(1)
        if len(calls) == 1:
            assert "another sync is running" in primary_sync.sync(root, dev)
        return real(root, dev)

    monkeypatch.setattr(primary_sync, "once", once)
    sha = rig.push()
    assert "moved from" in primary_sync.sync(rig.primary, DEV)
    assert len(calls) == 2 and rig.head() == sha


def test_a_commit_not_on_origin_yet_is_waited_for_then_followed(rig):
    sha = rig.commit()
    before = rig.head()
    proc = subprocess.Popen([sys.executable, str(SCRIPT), "--dev", DEV, "--sha", sha, *FAST], cwd=rig.primary,
                            stdout=subprocess.PIPE, text=True)
    time.sleep(1.0)
    assert proc.poll() is None and rig.head() == before
    rig.send()
    out = proc.communicate(timeout=60)[0]
    assert proc.returncode == 0 and "moved from" in out
    assert rig.head() == sha


def test_a_commit_that_never_reaches_origin_leaves_the_primary_as_it_is(rig):
    sha = rig.commit()
    before = rig.head()
    started = time.monotonic()
    assert f"{sha[:12]} did not reach origin/{DEV} in 1 s" in rig.sync("--sha", sha, "--wait-s", "1", "--poll-s", "0.2")
    assert time.monotonic() - started < 30 and rig.head() == before


def test_a_commit_that_origin_has_moved_past_counts_as_arrived(rig):
    first = rig.push()
    rig.push()
    assert "moved from" in rig.sync("--sha", first, *FAST)
    assert rig.head() == run_git(rig.pusher, "rev-parse", "HEAD")


def test_a_primary_whose_origin_cannot_be_reached_is_left_as_it_is(rig):
    before = rig.head()
    run_git(rig.primary, "remote", "set-url", "origin", str(rig.primary.parent / "gone.git"))
    assert "could not be fetched" in rig.sync()
    assert rig.head() == before


def test_the_script_runs_one_merge_and_nothing_that_moves_a_checkout():
    source = SCRIPT.read_text(encoding="utf-8")
    for word in ("stash", "checkout", "reset", "clean", "--force", "rebase", "pull", '"push"'):
        assert f'"{word}"' not in source, word
    assert source.count('"merge", "--ff-only"') == 1


def test_a_started_sync_follows_a_push_and_carries_no_git_redirects(rig, monkeypatch):
    sha = rig.push()
    monkeypatch.setattr("poolhouse.lan.on_the_persons_machine", lambda: True)
    with monkeypatch.context() as hostile:
        hostile.setenv("GIT_DIR", str(rig.primary.parent / "nowhere"))
        assert primary_sync.start(sha, DEV, rig.pusher) is True
    end = time.monotonic() + 60
    while rig.head() != sha and time.monotonic() < end:
        time.sleep(0.2)
    assert rig.head() == sha


def test_a_sync_starts_only_on_the_persons_machine(rig):
    sha = rig.push()
    before = rig.head()
    assert primary_sync.start(sha, DEV, rig.pusher) is False, "a test run is not the person's machine"
    assert rig.head() == before

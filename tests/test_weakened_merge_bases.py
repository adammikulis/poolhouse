"""weakened-assertions on a criss-cross history: two merge bases, and only what a merge or a branch itself drops is reported."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "hooks" / "weakened-assertions"
GUARD = "tests/test_x_guard.py"
NAMES = ("a", "b", "ours", "theirs", "shared")


def sh(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=False)


def git(cwd: Path, *args: str) -> str:
    done = sh(cwd, "git", *args)
    assert done.returncode == 0, (args, done.stdout, done.stderr)
    return done.stdout


def spread(*dropped: str, **replaced: str) -> str:
    """A guard test file of one function per name, each far enough from the next that edits merge cleanly. Each holds `assert <name>_kept`
    and `assert <name>_extra`; ``dropped`` lines are left out and ``replaced`` lines are written instead."""
    out = []
    for name in NAMES:
        lines = [f"def test_{name}():"]
        for kind in ("kept", "extra"):
            key = f"{name}_{kind}"
            if key not in dropped:
                lines.append(f"    assert {replaced.get(key, key)}")
        lines.append("    pass")
        out.append("\n".join(lines) + "\n")
    return "\n\n# ----\n#\n#\n#\n#\n\n".join(out)


def write(repo: Path, text: str, message: str) -> None:
    (repo / GUARD).write_text(text)
    git(repo, "add", GUARD)
    git(repo, "commit", "-qm", message, "--no-verify")


def criss_cross(tmp_path: Path, base: str, a: str, b: str, crossed: str) -> Path:
    """A repository whose branches `left` and `right` each merged the other's commit, so that both `a` and `b` are merge bases of
    them. ``crossed`` is the file both crossing merges hold."""
    repo = tmp_path / "cross"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "left")
    (repo / "tests").mkdir()
    write(repo, base, "base")
    git(repo, "branch", "right")
    write(repo, a, "a")
    git(repo, "tag", "a")
    git(repo, "checkout", "-q", "right")
    write(repo, b, "b")
    git(repo, "tag", "b")
    for branch, other in (("left", "b"), ("right", "a")):
        git(repo, "checkout", "-q", branch)
        sh(repo, "git", "merge", "--no-commit", "--no-ff", other)
        (repo / GUARD).write_text(crossed)
        git(repo, "add", GUARD)
        git(repo, "commit", "-qm", f"{branch} merges {other}", "--no-verify")
    return repo


def two_additions(tmp_path: Path) -> Path:
    """Each base added an assertion the other had not yet seen; each side then added one of its own."""
    none = ("a_extra", "b_extra", "ours_extra", "theirs_extra")
    repo = criss_cross(tmp_path, spread(*none), spread("b_extra", "ours_extra", "theirs_extra"),
                       spread("a_extra", "ours_extra", "theirs_extra"), spread("ours_extra", "theirs_extra"))
    git(repo, "checkout", "-q", "left")
    write(repo, spread("theirs_extra"), "left adds")
    git(repo, "checkout", "-q", "right")
    write(repo, spread("ours_extra"), "right adds")
    git(repo, "checkout", "-q", "left")
    assert len(git(repo, "merge-base", "--all", "left", "right").split()) == 2
    return repo


def staged(repo: Path) -> subprocess.CompletedProcess:
    return sh(repo, sys.executable, str(HOOK), "--staged")


def against(repo: Path, rev: str) -> subprocess.CompletedProcess:
    return sh(repo, sys.executable, str(HOOK), "--against", rev)


def test_a_clean_merge_over_two_merge_bases_is_accepted(tmp_path) -> None:
    repo = two_additions(tmp_path)
    git(repo, "merge", "--no-commit", "--no-ff", "right")
    assert (repo / GUARD).read_text() == spread()
    done = staged(repo)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("lost", ["a_extra", "b_extra", "shared_kept"])
def test_an_assertion_a_merge_over_two_merge_bases_drops_itself_is_reported(tmp_path, lost) -> None:
    repo = two_additions(tmp_path)
    git(repo, "merge", "--no-commit", "--no-ff", "right")
    (repo / GUARD).write_text(spread(lost))
    git(repo, "add", GUARD)
    done = staged(repo)
    assert done.returncode == 1 and "1 fewer assertions" in done.stderr, done.stderr


def conflicting(tmp_path: Path) -> Path:
    """The criss-cross above, whose two sides then rewrite one shared assertion differently."""
    repo = two_additions(tmp_path)
    write(repo, spread("theirs_extra", shared_kept="left_wording"), "left rewords")
    git(repo, "checkout", "-q", "right")
    write(repo, spread("ours_extra", shared_kept="right_wording"), "right rewords")
    git(repo, "checkout", "-q", "left")
    assert sh(repo, "git", "merge", "--no-commit", "--no-ff", "right").returncode == 1
    return repo


def test_a_conflict_over_two_merge_bases_resolved_without_loss_is_accepted(tmp_path) -> None:
    repo = conflicting(tmp_path)
    (repo / GUARD).write_text(spread(shared_kept="either_wording"))
    git(repo, "add", GUARD)
    done = staged(repo)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize("lost", ["shared_kept", "a_extra", "b_extra"])
def test_a_conflict_over_two_merge_bases_that_drops_an_assertion_is_reported(tmp_path, lost) -> None:
    repo = conflicting(tmp_path)
    (repo / GUARD).write_text(spread(lost) if lost == "shared_kept" else spread(lost, shared_kept="either_wording"))
    git(repo, "add", GUARD)
    done = staged(repo)
    assert done.returncode == 1 and "fewer assertions" in done.stderr, done.stderr


def test_a_conflict_between_the_two_merge_bases_themselves_resolved_down_is_still_reported(tmp_path) -> None:
    """The bases rewrote one assertion differently, so the history git merges them into is itself conflicted."""
    none = ("ours_extra", "theirs_extra")
    repo = criss_cross(tmp_path, spread(*none), spread(*none, shared_kept="a_says"), spread(*none, shared_kept="b_says"),
                       spread(*none, shared_kept="agreed"))
    git(repo, "checkout", "-q", "left")
    write(repo, spread(*none, shared_kept="left_says"), "left")
    git(repo, "checkout", "-q", "right")
    write(repo, spread(*none, shared_kept="right_says"), "right")
    git(repo, "checkout", "-q", "left")
    assert sh(repo, "git", "merge", "--no-commit", "--no-ff", "right").returncode == 1
    (repo / GUARD).write_text(spread(*none, "shared_kept"))
    git(repo, "add", GUARD)
    done = staged(repo)
    assert done.returncode == 1 and "fewer assertions" in done.stderr, done.stderr


def removals_in_both_bases(tmp_path: Path) -> Path:
    """Each base removed one assertion in its own commit; `right` is the development branch and `left` the branch under review."""
    repo = criss_cross(tmp_path, spread(), spread("a_extra"), spread("b_extra"), spread("a_extra", "b_extra"))
    git(repo, "checkout", "-q", "right")
    write(repo, spread("a_extra", "b_extra", theirs_kept="dev_edit"), "dev moves on")
    git(repo, "checkout", "-q", "left")
    write(repo, spread("a_extra", "b_extra", ours_kept="branch_edit"), "branch moves on")
    assert len(git(repo, "merge-base", "--all", "left", "right").split()) == 2
    return repo


def test_a_range_over_two_merge_bases_reports_no_removal_the_branch_did_not_make(tmp_path) -> None:
    done = against(removals_in_both_bases(tmp_path), "right")
    assert done.returncode == 0, done.stderr


def test_a_range_over_two_merge_bases_reports_what_the_branch_removed(tmp_path) -> None:
    repo = removals_in_both_bases(tmp_path)
    write(repo, spread("a_extra", "b_extra", "shared_extra", ours_kept="branch_edit"), "branch weakens")
    done = against(repo, "right")
    assert done.returncode == 1 and "1 fewer assertions" in done.stderr, done.stderr


def test_a_range_over_two_merge_bases_that_conflicts_still_reports_what_the_branch_removed(tmp_path) -> None:
    repo = removals_in_both_bases(tmp_path)
    write(repo, spread("a_extra", "b_extra", "shared_extra", ours_kept="branch_edit", theirs_kept="branch_edit_too"), "branch weakens")
    done = against(repo, "right")
    assert done.returncode == 1 and "fewer assertions" in done.stderr, done.stderr

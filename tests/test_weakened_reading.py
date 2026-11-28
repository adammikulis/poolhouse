"""weakened-assertions reads the whole repository from any folder of it, and never reports what it could not read as clean."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_weakened_merge_bases import HOOK, git, sh

GUARD = "tests/test_x_guard.py"
WHOLE = "def test_denied():\n    assert deny()\n    assert other()\n"
LESS = "def test_denied():\n    assert deny()\n"


def weakened(tmp_path: Path) -> Path:
    """A repository on `branch`, one commit past `dev`, that took an assertion from the guard test."""
    repo = tmp_path / "read"
    (repo / "tests").mkdir(parents=True)
    (repo / "docs").mkdir()
    git(repo, "init", "-q", "-b", "dev")
    (repo / GUARD).write_text(WHOLE)
    (repo / "docs" / "a.md").write_text("a\n")
    git(repo, "add", GUARD, "docs/a.md")
    git(repo, "commit", "-qm", "base", "--no-verify")
    git(repo, "checkout", "-qb", "branch")
    (repo / GUARD).write_text(LESS)
    git(repo, "add", GUARD)
    git(repo, "commit", "-qm", "change", "--no-verify")
    return repo


def check(folder: Path, *args: str):
    return sh(folder, sys.executable, str(HOOK), *args)


def lose(repo: Path, spec: str) -> None:
    """Delete the loose object ``spec`` names."""
    name = git(repo, "rev-parse", spec).strip()
    (repo / ".git" / "objects" / name[:2] / name[2:]).unlink()


@pytest.mark.redteam
@pytest.mark.parametrize("folder", ["docs", "tests"])
def test_a_range_read_from_a_folder_of_the_repository_lists_the_loss(tmp_path, folder) -> None:
    done = check(weakened(tmp_path) / folder, "--against", "dev")
    assert done.returncode == 1 and "1 fewer assertions" in done.stderr, (done.returncode, done.stderr)


@pytest.mark.redteam
def test_a_staged_check_from_a_folder_reads_the_message_it_was_given_there(tmp_path) -> None:
    repo = weakened(tmp_path)
    git(repo, "reset", "-q", "--soft", "dev")
    (repo / "docs" / "message").write_text("chore: trim\n")
    done = check(repo / "docs", "--staged", "--message", "message")
    assert done.returncode == 1 and "1 fewer assertions" in done.stderr, (done.returncode, done.stderr)
    (repo / "docs" / "message").write_text("chore: trim\n\nReviewed-by-second: somebody\n")
    assert check(repo / "docs", "--staged", "--message", "message").returncode == 0


@pytest.mark.redteam
@pytest.mark.parametrize("revision", ["no-such-commit", "", "dev..", "HEAD:tests"], ids=["unknown", "empty", "range", "tree"])
def test_a_range_that_names_no_commit_is_not_clean(tmp_path, revision) -> None:
    done = check(weakened(tmp_path), "--against", revision)
    assert done.returncode == 2 and "could not read" in done.stderr, (done.returncode, done.stderr)


@pytest.mark.redteam
def test_a_range_whose_diff_fails_is_not_clean(tmp_path) -> None:
    repo = weakened(tmp_path)
    lose(repo, f"branch:{GUARD}")
    done = check(repo, "--against", "dev")
    assert done.returncode == 2 and "could not read" in done.stderr, (done.returncode, done.stderr)


@pytest.mark.redteam
def test_a_staged_diff_that_fails_is_not_clean(tmp_path) -> None:
    repo = weakened(tmp_path)
    git(repo, "reset", "-q", "--soft", "dev")
    lose(repo, f":{GUARD}")
    (repo / GUARD).unlink()    # git reads a staged file from the working tree while the two agree
    done = check(repo, "--staged")
    assert done.returncode == 2 and "could not read" in done.stderr, (done.returncode, done.stderr)


@pytest.mark.redteam
def test_a_check_outside_a_repository_is_not_clean(tmp_path) -> None:
    done = check(tmp_path, "--staged")
    assert done.returncode == 2 and "could not read" in done.stderr, (done.returncode, done.stderr)

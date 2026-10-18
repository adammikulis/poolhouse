"""What git holds of a checkout: index bits that hide edits, and the paths its commit and index do not represent."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from poolhouse import gitstate

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(where: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(where), *args], env=ENV, text=True, capture_output=True, check=False)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "dev")
    (root / ".gitignore").write_text("*.key\nbuild/\n__pycache__/\n")
    (root / "d").mkdir()
    (root / "d" / "g").write_text("g")
    (root / "f").write_text("f")
    git(root, "add", ".gitignore", "d", "f")
    git(root, "commit", "-qm", "c", "--no-verify")
    return root


def test_a_plain_checkout_hides_nothing_and_holds_everything(repo):
    assert gitstate.hidden(repo) == []
    found = gitstate.unsaved(repo)
    assert (found.changed, found.ignored, found.hidden) == ([], [], []) and not found


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_an_index_bit_that_hides_edits_names_the_path(repo, flag):
    git(repo, "update-index", flag, "f")
    (repo / "f").write_text("edited")
    assert gitstate.hidden(repo) == ["f"] and gitstate.unsaved(repo).hidden == ["f"]
    assert gitstate.unsaved(repo).total == 1


def test_an_assume_unchanged_deletion_is_hidden(repo):
    git(repo, "update-index", "--assume-unchanged", "f")
    (repo / "f").unlink()
    assert gitstate.hidden(repo) == ["f"]


def test_a_sparse_checkout_is_represented_by_its_commit_and_an_edit_inside_it_is_a_change(repo):
    git(repo, "sparse-checkout", "set", "--no-cone", "d")
    assert not (repo / "f").exists() and not gitstate.unsaved(repo)
    (repo / "f").write_text("edited")
    assert gitstate.unsaved(repo).changed == ["f"]


def test_changed_untracked_and_ignored_paths_are_told_apart_and_rebuildable_ones_are_not_listed(repo):
    (repo / "f").write_text("edited")
    (repo / "new").write_text("n")
    (repo / "a.key").write_text("secret")
    (repo / "build").mkdir()
    (repo / "build" / "o").write_text("o")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "m.pyc").write_text("c")
    found = gitstate.unsaved(repo)
    assert sorted(found.changed) == ["f", "new"] and found.ignored == ["a.key"] and found.total == 3


def test_intent_to_add_is_a_change(repo):
    (repo / "new").write_text("n")
    git(repo, "add", "-N", "new")
    assert gitstate.unsaved(repo).changed == ["new"]


def test_a_submodule_edit_is_seen_whatever_the_submodule_ignore_setting_says(repo, tmp_path):
    inner = tmp_path / "inner"
    inner.mkdir()
    git(inner, "init", "-q", "-b", "dev")
    (inner / "x").write_text("x")
    git(inner, "add", "x")
    git(inner, "commit", "-qm", "c", "--no-verify")
    git(repo, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner), "sub")
    git(repo, "commit", "-qm", "sub", "--no-verify")
    git(repo, "config", "submodule.sub.ignore", "all")
    (repo / "sub" / "x").write_text("edited")
    assert gitstate.unsaved(repo).changed == ["sub"]


def test_a_directory_git_cannot_read_raises(tmp_path):
    with pytest.raises(RuntimeError):
        gitstate.hidden(tmp_path / "nowhere")


@pytest.mark.parametrize("name", ["__pycache__/m.pyc", "a/__pycache__/m.pyc", ".coverage.host.1", ".testmondata-wal", "build/x/y.o"])
def test_the_rebuildable_list_names_what_a_build_recreates(name):
    assert gitstate.rebuildable(name)


@pytest.mark.parametrize("name", ["private.key", "build/inner/", "build/.git/config", "notes.private", "__pycache__/data.json", "src/dist/x", ""])
def test_nothing_else_is_rebuildable(name):
    assert not gitstate.rebuildable(name)

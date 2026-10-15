"""Recovery preserves unowned or unique integration worktrees."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import land_recover as recover
from land_support import git

from poolhouse import trees


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    root, tree = tmp_path / "project", tmp_path / "integration"
    root.mkdir()
    git(root, "init", "-q", "-b", "dev")
    git(root, "config", "user.name", "Agent")
    git(root, "config", "user.email", "agent@example.invalid")
    git(root, "config", "commit.gpgsign", "false")
    (root / "tracked").write_text("initial")
    (root / ".gitignore").write_text("*.secret\n__pycache__/\n")
    git(root, "add", "tracked", ".gitignore")
    git(root, "commit", "-qm", "chore: seed")
    git(root, "worktree", "add", "-q", "-b", "land/fixture", str(tree))
    trees.claim(root, tree, "agent-fixture", time.time(), {"purpose": "integration batch"})
    claims = [SimpleNamespace(kind="worktree", key=str(tree), owner="agent-fixture", expires_in_s=900),
              SimpleNamespace(kind="branch", key="land/fixture", owner="agent-fixture", expires_in_s=900)]
    released = []
    session = SimpleNamespace(whoami=lambda: SimpleNamespace(name="agent-fixture"),
                              claims=lambda: claims, release=lambda *args: released.append(args))
    monkeypatch.setattr(recover, "connect", lambda **kwargs: session)
    return root, tree, claims, released


def test_owned_merged_clean_tree_removed_without_force(recovery):
    root, tree, _, released = recovery
    assert recover._sweep(root) == [str(tree), "land/fixture"]
    assert not tree.exists() and not git(root, "branch", "--list", "land/fixture")
    assert released == [("branch", "land/fixture"), ("worktree", str(tree))]


@pytest.mark.parametrize("obstacle", ["foreign", "missing-claim", "expired", "provenance", "locked", "dirty", "untracked", "ignored", "unique"])
def test_unproven_or_unique_tree_is_retained(recovery, obstacle):
    root, tree, claims, released = recovery
    if obstacle == "foreign":
        claims[0].owner = "other-agent"
    elif obstacle == "missing-claim":
        claims.pop()
    elif obstacle == "expired":
        claims[0].expires_in_s = 0
    elif obstacle == "provenance":
        trees.claim(root, tree, "other-agent", time.time(), {"purpose": "integration batch"})
    elif obstacle == "locked":
        git(root, "worktree", "lock", str(tree))
    elif obstacle == "dirty":
        (tree / "tracked").write_text("changed")
    elif obstacle == "untracked":
        (tree / "unique").write_text("preserve")
    elif obstacle == "ignored":
        (tree / "unique.secret").write_text("preserve")
    else:
        (tree / "tracked").write_text("unique commit")
        git(tree, "add", "tracked")
        git(tree, "commit", "-qm", "feat: unique")
    assert recover._sweep(root) == []
    assert tree.exists() and git(root, "branch", "--list", "land/fixture")
    assert not released


def test_removal_failure_preserves_branch_and_receipts(recovery, monkeypatch):
    root, tree, _, released = recovery
    actual = recover.lg.git
    def fail(root, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            return SimpleNamespace(returncode=1)
        return actual(root, *args, **kwargs)
    monkeypatch.setattr(recover.lg, "git", fail)
    assert recover._sweep(root) == [] and tree.exists() and not released

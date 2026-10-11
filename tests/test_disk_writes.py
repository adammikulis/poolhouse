"""Records that an unchanged rebind used to rewrite are left alone (docs/disk-writes.md)."""

import pytest

from poolhouse.workspace import integration_git as repo, worktree_lifecycle as lifecycle


@pytest.fixture
def checkout(tmp_path):
    primary = tmp_path / "repository"
    primary.mkdir()
    repo.git(primary, "init", "-b", "development")
    repo.git(primary, "config", "user.name", "Fixture")
    repo.git(primary, "config", "user.email", "fixture@example.test")
    (primary / "source.py").write_text("value = 1\n")
    repo.git(primary, "add", "source.py")
    repo.git(primary, "commit", "-m", "chore: fixture")
    made = tmp_path / "checkout"
    repo.git(primary, "worktree", "add", "-b", "worker/change", str(made))
    return made


def test_remembering_an_unchanged_checkout_does_not_rewrite_the_store(tmp_path, checkout):
    base = tmp_path / "lifecycle"
    base.mkdir()
    lifecycle.remember(base, "worker", str(checkout))
    database = base / "worktree-lifecycle.json"
    first = (database.stat().st_mtime_ns, database.read_bytes())
    lifecycle.remember(base, "worker", str(checkout))
    assert (database.stat().st_mtime_ns, database.read_bytes()) == first
    (checkout / "more.py").write_text("x = 1\n")
    repo.git(checkout, "add", "more.py")
    repo.git(checkout, "commit", "-m", "chore: more")
    lifecycle.remember(base, "worker", str(checkout))
    assert len(lifecycle.scopes(base, "worker")[0]["commits"]) == 2

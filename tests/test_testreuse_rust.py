"""Rust workspace inputs in conservative test reuse manifests."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import testnode
import testreuse_key as keys


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    source = root / "app/poolhouse-node/src/lib.rs"
    source.parent.mkdir(parents=True)
    source.write_text("pub const VALUE: u8 = 1;\n")
    (root / ".gitignore").write_text("app/target/\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "app/poolhouse-node/src/lib.rs", ".gitignore"], check=True)
    return root


def test_dirty_rust_changes_fallback_without_changing_head(project):
    before = keys.tree_digest(project)
    (project / "app/poolhouse-node/src/lib.rs").write_text("pub const VALUE: u8 = 2;\n")
    assert keys.tree_digest(project) != before


def test_untracked_rust_addition_and_deletion_change_fallback(project):
    before = keys.tree_digest(project)
    added = project / "app/poolhouse-node/src/new.rs"
    added.write_text("pub const EXTRA: u8 = 1;\n")
    assert keys.tree_digest(project) != before
    added.unlink()
    assert keys.tree_digest(project) == before
    (project / "app/poolhouse-node/src/lib.rs").unlink()
    assert keys.tree_digest(project) != before


def test_ignored_build_cache_does_not_change_fallback(project):
    before = keys.tree_digest(project)
    cache = project / "app/target/debug/ignored"
    cache.parent.mkdir(parents=True)
    cache.write_text("ignored artifact")
    assert keys.tree_digest(project) == before


def test_unreadable_git_inventory_refuses_reuse(tmp_path):
    (tmp_path / "app").mkdir()
    with pytest.raises(testnode.BuildError):
        keys.tree_digest(tmp_path)


def test_external_source_symlink_is_rejected_before_read(project, tmp_path, monkeypatch):
    outside = tmp_path / "private.txt"
    outside.write_text("private contents")
    linked = project / "app/poolhouse-node/src/linked.rs"
    linked.symlink_to(outside)
    original = keys.file_sha

    def guarded(path):
        assert path.resolve() != outside
        return original(path)

    monkeypatch.setattr(keys, "file_sha", guarded)
    with pytest.raises(ValueError, match="escapes checkout"):
        keys.tree_digest(project)


def test_broken_source_link_refuses_reuse(project):
    linked = project / "app/poolhouse-node/src/missing.rs"
    linked.symlink_to("absent.rs")
    with pytest.raises(FileNotFoundError):
        keys.tree_digest(project)


def test_same_bytes_symlink_retarget_changes_fallback(project):
    folder = project / "app/poolhouse-node/src"
    (folder / "other.rs").write_bytes((folder / "lib.rs").read_bytes())
    linked = folder / "linked.rs"
    linked.symlink_to("lib.rs")
    before = keys.tree_digest(project)
    linked.unlink()
    linked.symlink_to("other.rs")
    assert keys.tree_digest(project) != before


def test_source_mode_change_invalidates_fallback(project):
    before = keys.tree_digest(project)
    source = project / "app/poolhouse-node/src/lib.rs"
    source.chmod(source.stat().st_mode | 0o100)
    assert keys.tree_digest(project) != before


@pytest.mark.parametrize("selectors", [
    ("GIT_DIR",), ("GIT_WORK_TREE",), ("GIT_INDEX_FILE",),
    ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"),
])
def test_foreign_git_context_cannot_hide_sources(project, tmp_path, monkeypatch, selectors):
    tracked = project / "app/poolhouse-node/src/tracked.rs"
    tracked.write_text("pub const TRACKED: u8 = 1;\n")
    (project / ".gitignore").write_text("app/target/\ntracked.rs\n")
    subprocess.run(["git", "-C", str(project), "add", "-f", "app/poolhouse-node/src/tracked.rs"], check=True)
    foreign = tmp_path / "foreign"
    subprocess.run(["git", "init", "-q", str(foreign)], check=True)
    expected = testnode.sources(project / "app", existing_only=False)
    foreign_context = {"GIT_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign),
                       "GIT_INDEX_FILE": str(foreign / ".git/index")}
    for name in selectors:
        monkeypatch.setenv(name, foreign_context[name])
    assert testnode.sources(project / "app", existing_only=False) == expected
    before = keys.tree_digest(project)
    tracked.write_text("pub const TRACKED: u8 = 2;\n")
    assert keys.tree_digest(project) != before


def test_inventory_preserves_authentication_and_system_configuration(project, monkeypatch):
    preserved = {"GIT_SSH_COMMAND": "ssh -F /isolated/config", "GIT_CONFIG_SYSTEM": "/isolated/system-config",
                 "GIT_ASKPASS": "/isolated/askpass"}
    for name, value in preserved.items():
        monkeypatch.setenv(name, value)
    original = subprocess.run

    def checked(*args, **kwargs):
        environment = kwargs["env"]
        assert all(environment[name] == value for name, value in preserved.items())
        return original(*args, **kwargs)

    monkeypatch.setattr(testnode.subprocess, "run", checked)
    assert testnode.sources(project / "app") == ["poolhouse-node/src/lib.rs"]

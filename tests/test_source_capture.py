"""Private regular-file source capture and mutation refusals."""

import hashlib
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from poolhouse.activity import source_snapshot as source

pytestmark = pytest.mark.redteam


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "protected_roots", lambda environment: (tmp_path / "secrets",))
    root = tmp_path / "repo"
    root.mkdir()
    storage = tmp_path / "private"
    storage.mkdir(mode=0o700)
    program = source.actual_git()

    def git(*args):
        return subprocess.run([str(program), *args], cwd=root, env=source.git_environment(),
                              check=True, capture_output=True)

    git("init", "--initial-branch=source")
    (root / "tracked").write_bytes(b"tracked\0bytes")
    git("add", "tracked")
    return root, storage, git


def capture(repo, target="capture", **options):
    root, storage, _ = repo
    snapshot = source.SourceSnapshot(root, storage)
    try:
        return snapshot.capture(storage / target, **options)
    finally:
        snapshot.close()


def test_capture_preserves_raw_bytes_and_frozen_manifest_without_git_configuration(repo):
    root, storage, _ = repo
    (root / "nested").mkdir()
    (root / "nested" / "script").write_bytes(b"#!/bin/sh\nexit 0\n")
    (root / "nested" / "script").chmod(0o700)
    marker = storage / "executed"
    (root / ".git" / "config").write_text(
        f"[core]\n fsmonitor = touch {marker}\n[filter \"bad\"]\n clean = touch {marker}\n")
    (root / ".gitattributes").write_text("* filter=bad\n")
    result = capture(repo, excluded=(b".gitattributes",))
    assert not marker.exists()
    assert not (result.directory / ".git").exists()
    assert (result.directory / "tracked").read_bytes() == b"tracked\0bytes"
    assert (result.directory / "nested" / "script").read_bytes().endswith(b"exit 0\n")
    entries = {entry.path: entry for entry in result.entries}
    assert entries[b"tracked"].sha256 == hashlib.sha256(b"tracked\0bytes").digest()
    assert entries[b"nested/script"].mode == b"100755"
    assert result.excluded == (b".gitattributes",)
    assert not result.missing
    with pytest.raises(FrozenInstanceError):
        result.tree_hash = "forged"
    with pytest.raises(FrozenInstanceError):
        result.entries[0].size = 100


def test_exclusions_and_missing_tracked_files_are_distinct(repo):
    root, _, git = repo
    (root / ".env").write_text("fixture-secret")
    (root / "deleted").write_text("old")
    git("add", ".env", "deleted")
    (root / "deleted").unlink()
    result = capture(repo, excluded=(b".env",))
    assert result.excluded == (b".env",)
    assert result.missing == (b"deleted",)
    assert not (result.directory / ".env").exists()
    assert b".env" not in result.missing


@pytest.mark.parametrize("original_mode,changed_mode", [(0o600, 0o700), (0o700, 0o600)])
def test_capture_rejects_changed_executable_mode(repo, monkeypatch, original_mode, changed_mode):
    root, storage, _ = repo
    (root / "tracked").chmod(original_mode)
    original = source.SourceSnapshot._verify_output

    def changed(snapshot, descriptor, entries):
        (storage / "capture" / "tracked").chmod(changed_mode)
        return original(snapshot, descriptor, entries)

    monkeypatch.setattr(source.SourceSnapshot, "_verify_output", changed)
    with pytest.raises(RuntimeError, match="capture output"):
        capture(repo)
    assert not (storage / "capture").exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "writable"])
def test_unsafe_inputs_abort_without_output(repo, kind):
    root, storage, _ = repo
    path = root / "tracked"
    if kind == "symlink":
        path.unlink()
        path.symlink_to("other")
        (root / "other").write_text("other")
    elif kind == "hardlink":
        os.link(path, root / "alias")
    elif kind == "fifo":
        path.unlink()
        os.mkfifo(path)
    else:
        path.chmod(0o666)
    with pytest.raises(RuntimeError, match="regular files"):
        capture(repo)
    assert list(storage.iterdir()) == []


@pytest.mark.parametrize("attack", ["rewrite", "delete", "add", "index", "symlink", "manifest"])
def test_changes_during_copy_abort_and_remove_partial_output(repo, monkeypatch, attack):
    root, storage, git = repo
    original = source.SourceSnapshot.blob

    def changed(snapshot, path, info, target=None):
        value = original(snapshot, path, info, target)
        if attack == "rewrite":
            path.write_bytes(b"changed bytes")
        elif attack == "delete":
            path.unlink()
        elif attack == "add":
            (root / "added").write_text("new")
        elif attack == "index":
            git("update-index", "--chmod=+x", "tracked")
        elif attack == "symlink":
            path.unlink()
            path.symlink_to(storage / "outside")
        else:
            snapshot.entries.clear()
        return value

    monkeypatch.setattr(source.SourceSnapshot, "blob", changed)
    with pytest.raises(RuntimeError, match="changed"):
        capture(repo)
    assert list(storage.iterdir()) == []


@pytest.mark.parametrize("attack", ["hardlink", "symlink", "additional", "rewrite"])
def test_output_changes_are_refused(repo, monkeypatch, attack):
    _, storage, _ = repo
    original = source.SourceSnapshot._verify_capture

    def changed(snapshot, *args):
        original(snapshot, *args)
        target = storage / "capture" / "tracked"
        if attack == "hardlink":
            os.link(target, storage / "alias")
        elif attack == "symlink":
            target.unlink()
            target.symlink_to(storage / "elsewhere")
        elif attack == "additional":
            (target.parent / "additional").write_text("unexpected")
        else:
            target.write_bytes(b"different bytes")

    monkeypatch.setattr(source.SourceSnapshot, "_verify_capture", changed)
    with pytest.raises((OSError, RuntimeError)):
        capture(repo)
    assert not (storage / "capture").exists()
    assert not list(storage.glob("source-git-*"))


def test_target_replacement_preserves_foreign_data(repo, monkeypatch):
    _, storage, _ = repo
    original = source.SourceSnapshot._verify_capture

    def replaced(snapshot, *args):
        original(snapshot, *args)
        (storage / "capture").rename(storage / "moved")
        (storage / "capture").mkdir()
        (storage / "capture" / "foreign").write_text("preserve")

    monkeypatch.setattr(source.SourceSnapshot, "_verify_capture", replaced)
    with pytest.raises(RuntimeError, match="target changed"):
        capture(repo)
    assert (storage / "capture" / "foreign").read_text() == "preserve"


@pytest.mark.parametrize("limit", ["FILE_LIMIT", "TOTAL_LIMIT"])
def test_existing_byte_limits_apply_to_capture(repo, monkeypatch, limit):
    _, storage, _ = repo
    monkeypatch.setattr(source, limit, 1)
    with pytest.raises(RuntimeError, match="bounds"):
        capture(repo)
    assert list(storage.iterdir()) == []


def test_capture_refuses_overlap_and_existing_target(repo):
    root, storage, _ = repo
    snapshot = source.SourceSnapshot(root, storage)
    try:
        with pytest.raises(RuntimeError, match="overlaps"):
            snapshot.capture(root / "inside")
        existing = storage / "existing"
        existing.mkdir()
        (existing / "keep").write_text("keep")
        with pytest.raises(FileExistsError):
            snapshot.capture(existing)
        assert (existing / "keep").read_text() == "keep"
    finally:
        snapshot.close()


def test_byte_encoded_names_remain_distinct(repo):
    root, _, _ = repo
    names = ("雪".encode(), b"a\nnewline")
    for name in names:
        fd = os.open(os.fsencode(root) + b"/" + name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
    result = capture(repo)
    assert {entry.path for entry in result.entries} == {b"tracked", *names}


def test_unsupported_byte_names_do_not_collapse_in_source_paths(repo):
    root, storage, _ = repo
    snapshot = source.SourceSnapshot(root, storage)
    try:
        names = (b"a\xff", b"a\xfe")
        assert tuple(os.fsencode(snapshot.path(name).relative_to(root)) for name in names) == names
    finally:
        snapshot.close()


@pytest.mark.parametrize("target", ["missing", "index"])
def test_dangling_symlink_never_counts_as_missing(repo, monkeypatch, target):
    root, _, git = repo
    (root / "nested").mkdir()
    missing = root / "nested" / "missing"
    missing.write_text("old")
    git("add", "nested/missing")
    missing.unlink()
    original = source.SourceSnapshot._verify_capture

    def changed(snapshot, *args):
        path = missing if target == "missing" else root / ".git" / "index"
        if path.exists():
            path.unlink()
        path.symlink_to(root / "nonexistent")
        return original(snapshot, *args)

    monkeypatch.setattr(source.SourceSnapshot, "_verify_capture", changed)
    with pytest.raises(RuntimeError, match="changed"):
        capture(repo)


def test_fifo_swap_does_not_block_source_open(repo):
    root, storage, _ = repo
    code = '''import os,sys
from pathlib import Path
from poolhouse.activity import source_snapshot as source
root, storage = map(Path, sys.argv[1:])
source.protected_roots = lambda environment: (root.parent / 'secrets',)
snapshot = source.SourceSnapshot(root, storage)
original = os.open
def swapped(path, flags, *args, **kwargs):
    if os.fspath(path) == str(root / 'tracked'):
        os.unlink(path)
        os.mkfifo(path)
    return original(path, flags, *args, **kwargs)
os.open = swapped
try:
    snapshot.capture(storage / 'capture')
except RuntimeError as error:
    print(str(error))
    sys.exit(23)
finally:
    snapshot.close()
'''
    environment = source.git_environment()
    environment["PYTHONPATH"] = str(Path(source.__file__).resolve().parents[2])
    result = subprocess.run([sys.executable, "-c", code, str(root), str(storage)], env=environment,
                            capture_output=True, timeout=10)
    assert result.returncode == 23 and b"changed before read" in result.stdout
    assert not (storage / "capture").exists()


@pytest.mark.parametrize("present", [False, True])
def test_retained_paths_cover_absence_and_ignored_recreation(repo, present):
    root, storage, _ = repo
    (root / ".gitignore").write_text("retained\n")
    if present:
        (root / "retained").write_bytes(b"recreated bytes")
    snapshot = source.SourceSnapshot(root, storage, known_paths=(b"retained",))
    try:
        result = snapshot.capture(storage / "retained-result")
    finally:
        snapshot.close()
    assert result.missing == (() if present else (b"retained",))
    if present:
        assert (result.directory / "retained").read_bytes() == b"recreated bytes"


@pytest.mark.parametrize("names", [
    [b"file"], ("file",), (b"",), (b"../outside",), (b"a/../outside",), (b"/outside",),
    (b"a//b",), (b"a/./b",), (b"a\0b",), (b".git/config",), (b"a/.GiT/config",),
    (b"same", b"same"), (b"z", b"a"), (b"Name", b"name"),
    (b"e\xcc\x81", b"\xc3\xa9"),
])
def test_retained_paths_refuse_malformed_or_ambiguous_inventory(repo, names):
    root, storage, _ = repo
    before = set(storage.iterdir())
    with pytest.raises((RuntimeError, ValueError)):
        source.SourceSnapshot(root, storage, known_paths=names)
    assert set(storage.iterdir()) == before


def test_retained_inventory_obeys_existing_encoded_byte_limit(repo):
    root, storage, _ = repo
    with pytest.raises(RuntimeError, match="bounded canonical"):
        source.SourceSnapshot(root, storage, known_paths=(b"x" * source.OUTPUT_LIMIT,))
    assert not list(storage.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_recreated_ignored_retained_paths_cannot_change_type(repo, kind):
    root, storage, _ = repo
    (root / ".gitignore").write_text("retained\n")
    path = root / "retained"
    if kind == "symlink":
        path.symlink_to(root / "tracked")
    elif kind == "hardlink":
        os.link(root / "tracked", path)
    else:
        os.mkfifo(path)
    snapshot = source.SourceSnapshot(root, storage, known_paths=(b"retained",))
    try:
        with pytest.raises(RuntimeError, match="protected regular"):
            snapshot.capture(storage / "rejected-result")
    finally:
        snapshot.close()
    assert not list(storage.iterdir())


def test_retained_paths_cannot_override_exclusions(repo):
    root, storage, _ = repo
    (root / "retained").write_text("private configuration")
    snapshot = source.SourceSnapshot(root, storage, known_paths=(b"retained",))
    try:
        result = snapshot.capture(storage / "excluded-result", excluded=(b"retained",))
    finally:
        snapshot.close()
    assert b"retained" not in result.missing
    assert all(entry.path != b"retained" for entry in result.entries)
    assert not (result.directory / "retained").exists()


@pytest.mark.parametrize("nested", [False, True])
def test_absent_retained_path_appearing_during_capture_refuses_output(repo, monkeypatch, nested):
    root, storage, _ = repo
    name = b"nested/retained" if nested else b"retained"
    target = root / os.fsdecode(name)
    target.parent.mkdir(exist_ok=True)
    (root / ".gitignore").write_text(os.fsdecode(name) + "\n")
    snapshot = source.SourceSnapshot(root, storage, known_paths=(name,))
    original = snapshot._verify_capture

    def changed(*args):
        target.write_text("concurrent")
        original(*args)

    monkeypatch.setattr(snapshot, "_verify_capture", changed)
    try:
        with pytest.raises(RuntimeError, match="changed"):
            snapshot.capture(storage / "changed-result")
    finally:
        snapshot.close()
    assert not list(storage.iterdir())

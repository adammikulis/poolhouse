"""A JSON file is rewritten whole, or not at all."""

import errno
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_hub_discover import symlink

from poolhouse.files import (
    UNVERSIONED,
    CrossDevice,
    promote,
    prune_orphans,
    read_json,
    reading,
    version_of,
    versioned,
    write_json,
    write_text,
    writing,
)


def test_what_was_written_is_read_back_and_nothing_is_left_beside_it(tmp_path):
    p = tmp_path / "state" / "graph.json"
    write_json(p, {"who": "Bea Marlow", "n": 1})
    write_json(p, {"who": "Bea Marlow", "n": 2})
    assert read_json(p, None) == {"who": "Bea Marlow", "n": 2}
    assert [f.name for f in p.parent.iterdir()] == ["graph.json"]


def test_a_write_that_fails_leaves_the_old_file_and_no_temporary(tmp_path):
    p = tmp_path / "graph.json"
    write_json(p, {"n": 1})
    with pytest.raises(TypeError):
        write_json(p, {"n": object()})
    assert json.loads(p.read_text()) == {"n": 1}
    assert [f.name for f in tmp_path.iterdir()] == ["graph.json"]


def test_a_missing_or_broken_file_reads_as_the_default(tmp_path):
    assert read_json(tmp_path / "absent.json", {"_v": 1}) == {"_v": 1}
    (tmp_path / "broken.json").write_text("{not json")
    assert read_json(tmp_path / "broken.json", []) == []


def test_text_is_kept_as_written_not_escaped(tmp_path):
    p = tmp_path / "t.json"
    write_json(p, {"place": "Zürich"})
    assert "Zürich" in p.read_text(encoding="utf-8")


def test_a_file_for_a_record_the_log_no_longer_has_is_deleted(tmp_path):
    d = tmp_path / "extractions"
    d.mkdir()
    (d / "C1-1.000001.json").write_text("{}")
    (d / "C1-9.999999.json").write_text("{}")
    (d / "notes.txt").write_text("not a record")
    assert prune_orphans(d, {"C1-1.000001"}) == ["C1-9.999999"]
    assert sorted(f.name for f in d.iterdir()) == ["C1-1.000001.json", "notes.txt"]


def test_a_directory_that_does_not_exist_has_no_orphans(tmp_path):
    assert prune_orphans(tmp_path / "nowhere", set()) == []


def test_a_finished_file_takes_the_targets_place_in_one_step(tmp_path):
    target = tmp_path / "model.gguf"
    target.write_bytes(b"the old one")
    (tmp_path / "model.gguf.part").write_bytes(b"the new one")
    assert promote(tmp_path / "model.gguf.part", target) == target
    assert target.read_bytes() == b"the new one"
    assert [f.name for f in tmp_path.iterdir()] == ["model.gguf"]


def test_a_directory_and_a_symlink_are_promoted_the_same_way(tmp_path):
    staging = tmp_path / "staging"
    (staging / "weights").mkdir(parents=True)
    promote(staging, tmp_path / "checkpoint-100")
    assert (tmp_path / "checkpoint-100" / "weights").is_dir()

    symlink(tmp_path / "latest.tmp", "checkpoint-100", directory=True)
    promote(tmp_path / "latest.tmp", tmp_path / "latest")
    assert (tmp_path / "latest").is_symlink()
    assert (tmp_path / "latest" / "weights").is_dir()


def test_a_move_across_filesystems_says_so_rather_than_copying(tmp_path, monkeypatch):
    """A shared model cache is tens of gigabytes and the disk has them once, so the caller
    has to be able to tell this apart from any other OSError."""
    def elsewhere(_source, _target):
        raise OSError(errno.EXDEV, "Cross-device link")

    monkeypatch.setattr("poolhouse.files.replace", elsewhere)
    (tmp_path / "cache").mkdir()
    with pytest.raises(CrossDevice, match="different filesystems"):
        promote(tmp_path / "cache", tmp_path / "shared")
    assert (tmp_path / "cache").is_dir()


def test_an_interrupted_write_leaves_the_previous_file_whole(tmp_path):
    p = tmp_path / "reads.json"
    p.write_text('{"1.1": {"unit": "1.1"}}')
    with pytest.raises(KeyboardInterrupt), writing(p) as tmp:
        tmp.write_text("half of the ne")
        raise KeyboardInterrupt("killed part-way through")
    assert json.loads(p.read_text()) == {"1.1": {"unit": "1.1"}}
    assert [f.name for f in tmp_path.iterdir()] == ["reads.json"]


def test_text_arrives_whole_or_not_at_all(tmp_path):
    p = tmp_path / "rows.jsonl"
    write_text(p, '{"id": 1}\n')
    assert p.read_text() == '{"id": 1}\n'
    assert [f.name for f in tmp_path.iterdir()] == ["rows.jsonl"]


def test_a_record_says_which_shape_it_has(tmp_path):
    p = tmp_path / "stamp.json"
    write_json(p, versioned({"url": "https://example.invalid/m.gguf"}, 1))
    assert read_json(p, None) == {"version": 1, "url": "https://example.invalid/m.gguf"}
    assert version_of(read_json(p, None)) == 1


def test_a_record_written_before_the_key_existed_reads_as_unversioned(tmp_path):
    p = tmp_path / "old.json"
    p.write_text('{"url": "https://example.invalid/m.gguf"}')
    assert version_of(read_json(p, None)) == UNVERSIONED
    assert version_of("not a record") == UNVERSIONED
    assert version_of({"version": "not a number"}) == UNVERSIONED


def test_sha256_file_matches_hashlib_across_chunk_boundaries(tmp_path):
    import hashlib

    from poolhouse.files import sha256_file

    data = bytes(range(256)) * 1000
    path = tmp_path / "blob.bin"
    path.write_bytes(data)
    assert sha256_file(path, chunk=4096) == sha256_file(path) == hashlib.sha256(data).hexdigest()


def test_status_updates_preserve_open_and_concurrent_snapshots(tmp_path):
    from workspace_kit import run_python

    from poolhouse.workspace import localagent

    ws = SimpleNamespace(base=tmp_path / "workspace")
    status = localagent.Status(ws.base, "worker")
    status.update(state="paused", counter=0, payload="0" * 8192)
    initial = localagent.status_of(ws.base, "worker")
    ready = threading.Barrier(5)
    stopped = threading.Event()

    def read_snapshots():
        ready.wait(timeout=10)
        count = 0
        while not stopped.is_set():
            row = localagent.status_of(ws.base, "worker")
            assert row["payload"] == str(row["counter"]) * 8192
            assert row["state"] in {"paused", "running"}
            count += 1
        return count

    code = '''import sys
from pathlib import Path
from poolhouse.workspace.localagent import Status
status = Status(Path(sys.argv[1]), "worker")
for counter in range(1, 51):
    status.update(state="running", counter=counter, payload=str(counter) * 8192)
'''
    with reading(status.path) as snapshot, ThreadPoolExecutor(max_workers=4) as pool:
        readers = [pool.submit(read_snapshots) for _ in range(4)]
        ready.wait(timeout=10)
        try:
            result = run_python(code, ws.base, "", str(ws.base), timeout=30)
            assert result.returncode == 0, result.stderr
        finally:
            stopped.set()
        assert all(reader.result(timeout=10) > 0 for reader in readers)
        assert json.load(snapshot) == initial
    assert localagent.status_of(ws.base, "worker")["counter"] == 50
    assert list(status.path.parent.glob("*.tmp")) == []


@pytest.fixture
def promotion_dirs(tmp_path):
    import os
    from contextlib import ExitStack

    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    with ExitStack() as closing:
        descriptors = []
        for directory in (source, target):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            closing.callback(os.close, descriptor)
            descriptors.append(descriptor)
        yield source, target, descriptors


def test_new_promotion_uses_independent_pinned_directories(promotion_dirs):
    import os

    from poolhouse.files import promote_new

    source, target, descriptors = promotion_dirs
    (source / "ready").write_bytes(b"finished bytes")
    (source / "ready").chmod(0o700)
    before = (source / "ready").stat()
    promote_new(b"ready", b"result", source_fd=descriptors[0], target_fd=descriptors[1])
    assert not os.path.lexists(source / "ready")
    after = (target / "result").stat()
    assert (after.st_dev, after.st_ino, after.st_mode) == (before.st_dev, before.st_ino, before.st_mode)
    assert (target / "result").read_bytes() == b"finished bytes"


@pytest.mark.parametrize("kind", ["file", "directory", "dangling"])
@pytest.mark.parametrize("relative", [False, True])
def test_new_promotion_never_replaces_existing_names(promotion_dirs, kind, relative):
    import os

    from poolhouse.files import promote_new

    source, target, descriptors = promotion_dirs
    (source / "ready").write_bytes(b"candidate")
    destination = target / "result"
    if kind == "file":
        destination.write_bytes(b"owner bytes")
    elif kind == "directory":
        destination.mkdir()
        (destination / "owner").write_bytes(b"owner bytes")
    else:
        destination.symlink_to("absent-owner-file")
    before = destination.lstat()
    args = ("ready", "result") if relative else (source / "ready", destination)
    kwargs = {"source_fd": descriptors[0], "target_fd": descriptors[1]} if relative else {}
    with pytest.raises(FileExistsError):
        promote_new(*args, **kwargs)
    assert destination.lstat() == before
    assert (source / "ready").read_bytes() == b"candidate"
    assert os.path.lexists(destination)


def test_new_promotion_keeps_pinned_inode_scope_after_ancestor_redirect(promotion_dirs):
    from poolhouse.files import promote_new

    source, target, descriptors = promotion_dirs
    (source / "ready").write_bytes(b"prepared")
    retained_source, retained_target = source.with_name("retained-source"), target.with_name("retained-target")
    source.rename(retained_source)
    target.rename(retained_target)
    foreign = source.with_name("foreign")
    foreign.mkdir()
    (foreign / "ready").write_bytes(b"foreign source")
    (foreign / "result").write_bytes(b"foreign result")
    source.symlink_to(foreign, target_is_directory=True)
    target.symlink_to(foreign, target_is_directory=True)
    promote_new("ready", "result", source_fd=descriptors[0], target_fd=descriptors[1])
    assert (retained_target / "result").read_bytes() == b"prepared"
    assert (foreign / "ready").read_bytes() == b"foreign source"
    assert (foreign / "result").read_bytes() == b"foreign result"


@pytest.mark.parametrize("name", ["", ".", "..", "../ready", "/ready", "part/ready", "./ready", "ready/", "a\0b", b"\xff/x"])
@pytest.mark.parametrize("side", ["source", "target"])
def test_new_promotion_rejects_unmodified_relative_names(promotion_dirs, name, side):
    from poolhouse.files import promote_new

    source, target, descriptors = promotion_dirs
    (source / "ready").write_bytes(b"prepared")
    args = (name, "result") if side == "source" else ("ready", name)
    with pytest.raises(ValueError):
        promote_new(*args, source_fd=descriptors[0], target_fd=descriptors[1])
    assert (source / "ready").read_bytes() == b"prepared"
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("bad", [True, False, -1, "3"])
@pytest.mark.parametrize("side", [0, 1])
def test_new_promotion_rejects_invalid_descriptor_types(promotion_dirs, bad, side):
    from poolhouse.files import promote_new

    source, target, descriptors = promotion_dirs
    (source / "ready").write_bytes(b"prepared")
    chosen = list(descriptors)
    chosen[side] = bad
    with pytest.raises(ValueError):
        promote_new("ready", "result", source_fd=chosen[0], target_fd=chosen[1])
    assert (source / "ready").read_bytes() == b"prepared"
    assert list(target.iterdir()) == []


def test_new_promotion_rejects_missing_closed_and_regular_file_descriptors(promotion_dirs):
    import os

    from poolhouse.files import promote_new

    source, target, descriptors = promotion_dirs
    candidate = source / "ready"
    candidate.write_bytes(b"prepared")
    with pytest.raises(ValueError):
        promote_new("ready", "result", source_fd=descriptors[0])
    with pytest.raises(ValueError):
        promote_new("ready", "result", target_fd=descriptors[1])
    descriptor = os.open(candidate, os.O_RDONLY)
    try:
        with pytest.raises(ValueError):
            promote_new("ready", "result", source_fd=descriptor, target_fd=descriptors[1])
    finally:
        os.close(descriptor)
    with pytest.raises(OSError) as error:
        promote_new("ready", "result", source_fd=descriptor, target_fd=descriptors[1])
    assert error.value.errno == errno.EBADF
    assert candidate.read_bytes() == b"prepared"
    assert list(target.iterdir()) == []


def test_new_promotion_moves_symlink_itself_without_following(promotion_dirs):
    from poolhouse.files import promote_new

    source, target, descriptors = promotion_dirs
    owner = source / "owner"
    owner.write_bytes(b"owner content")
    (source / "ready").symlink_to(owner)
    promote_new("ready", "result", source_fd=descriptors[0], target_fd=descriptors[1])
    assert (target / "result").is_symlink()
    assert (target / "result").readlink() == owner
    assert owner.read_bytes() == b"owner content"


def test_new_promotion_competing_creators_have_one_winner(promotion_dirs):
    from poolhouse.files import promote_new

    source, target, descriptors = promotion_dirs
    ready = threading.Barrier(8)
    for index in range(8):
        (source / str(index)).write_text(str(index))
    def publish(index):
        ready.wait(timeout=10)
        try:
            promote_new(str(index), "result", source_fd=descriptors[0], target_fd=descriptors[1])
        except FileExistsError:
            return False
        return True
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(publish, range(8)))
    assert sum(outcomes) == 1
    winner = outcomes.index(True)
    assert (target / "result").read_text() == str(winner)
    assert sorted(path.name for path in source.iterdir()) == [str(index) for index in range(8) if index != winner]


def test_new_promotion_missing_destination_parent_preserves_source(promotion_dirs):
    from poolhouse.files import promote_new

    source, target, _descriptors = promotion_dirs
    (source / "ready").write_bytes(b"prepared")
    with pytest.raises(FileNotFoundError):
        promote_new(source / "ready", target / "missing" / "result")
    assert (source / "ready").read_bytes() == b"prepared"
    assert list(target.iterdir()) == []


def test_new_promotion_unsupported_platform_has_no_fallback(promotion_dirs, monkeypatch):
    from poolhouse import files

    source, target, descriptors = promotion_dirs
    (source / "ready").write_bytes(b"prepared")
    monkeypatch.setattr(files.sys, "platform", "unsupported")
    with pytest.raises(OSError) as error:
        files.promote_new("ready", "result", source_fd=descriptors[0], target_fd=descriptors[1])
    assert error.value.errno == errno.ENOTSUP
    assert (source / "ready").read_bytes() == b"prepared"
    assert list(target.iterdir()) == []


def test_new_promotion_cross_device_error_never_copies(promotion_dirs, monkeypatch):
    import ctypes

    from poolhouse import files

    source, target, descriptors = promotion_dirs
    (source / "ready").write_bytes(b"prepared")
    def elsewhere(*_arguments):
        ctypes.set_errno(errno.EXDEV)
        return -1
    library = SimpleNamespace(renameatx_np=elsewhere, renameat2=elsewhere)
    monkeypatch.setattr(files.ctypes, "CDLL", lambda *_args, **_kwargs: library)
    with pytest.raises(CrossDevice):
        files.promote_new("ready", "result", source_fd=descriptors[0], target_fd=descriptors[1])
    assert (source / "ready").read_bytes() == b"prepared"
    assert list(target.iterdir()) == []


@pytest.mark.parametrize("side", ["source", "target"])
@pytest.mark.parametrize("encoded", [False, True])
def test_new_promotion_path_mode_rejects_embedded_null_without_truncation(promotion_dirs, side, encoded):
    import os

    from poolhouse.files import promote_new

    source, target, _descriptors = promotion_dirs
    candidate, destination = source / "ready", target / "result"
    candidate.write_bytes(b"prepared")
    arguments = [str(candidate), str(destination)]
    arguments[0 if side == "source" else 1] += "\0ignored"
    if encoded:
        arguments = [os.fsencode(argument) for argument in arguments]
    with pytest.raises(ValueError, match="null"):
        promote_new(*arguments)
    assert candidate.read_bytes() == b"prepared"
    assert list(target.iterdir()) == []

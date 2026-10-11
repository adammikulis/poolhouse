"""``hfstore``: naming a path inside the hub cache, comparing bytes, and putting a file into ``blobs/``."""

from __future__ import annotations

import errno
import hashlib
import os

import pytest

from poolhouse.hub import hfstore

COMMIT = "c" * 40
BODY = bytes(range(256)) * 64


def test_a_path_inside_a_snapshot_names_its_repository_commit_and_file(tmp_path):
    folder = tmp_path / "hub" / "models--maker--thing-GGUF" / "snapshots" / COMMIT
    assert hfstore.identify(folder) == ("maker/thing-GGUF", COMMIT, "")
    assert hfstore.identify(folder / "MTP" / "m.gguf") == ("maker/thing-GGUF", COMMIT, "MTP/m.gguf")


def test_a_path_that_is_not_in_a_snapshot_names_nothing(tmp_path):
    root = tmp_path / "hub" / "models--maker--thing"
    assert hfstore.identify(root / "blobs" / ("a" * 64)) is None
    assert hfstore.identify(root / "snapshots" / "main" / "x") is None
    assert hfstore.identify(root / "refs" / "main") is None
    assert hfstore.identify(tmp_path / "plain" / "folder") is None
    assert hfstore.identify(tmp_path / "hub" / "models--lonely" / "snapshots" / COMMIT) is None


def test_two_files_are_the_same_only_when_every_byte_is(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.write_bytes(BODY)
    two.write_bytes(BODY)
    assert hfstore.same_bytes(one, two)
    two.write_bytes(BODY[:-1] + b"\x00")
    assert not hfstore.same_bytes(one, two)
    two.write_bytes(BODY[:-1])
    assert not hfstore.same_bytes(one, two)
    two.write_bytes(BODY + b"\x00")
    assert not hfstore.same_bytes(one, two)
    two.write_bytes(b"\x01" + BODY[1:])
    assert not hfstore.same_bytes(one, two)


def test_bytes_are_compared_through_a_link(tmp_path):
    blob, link, other = tmp_path / "blob", tmp_path / "link", tmp_path / "other"
    blob.write_bytes(BODY)
    link.symlink_to(blob)
    other.write_bytes(BODY)
    assert hfstore.same_bytes(link, other)


def test_a_digest_is_read_in_the_form_the_name_is_written_in(tmp_path):
    path = tmp_path / "f"
    path.write_bytes(BODY)
    assert hfstore.digest_of(path, "a" * 64) == hashlib.sha256(BODY).hexdigest()
    git = hashlib.sha1(b"blob %d\0" % len(BODY) + BODY, usedforsecurity=False).hexdigest()
    assert hfstore.digest_of(path, "a" * 40) == git


def test_a_file_is_linked_into_blobs_without_a_copy(tmp_path):
    source, blob = tmp_path / "old" / "f", tmp_path / "blobs" / "name"
    source.parent.mkdir()
    source.write_bytes(BODY)
    name = hashlib.sha256(BODY).hexdigest()
    assert hfstore.into_blobs(source, blob, name) == "new"
    assert source.samefile(blob) and blob.read_bytes() == BODY


def test_a_blob_that_is_already_these_bytes_is_kept(tmp_path):
    source, blob = tmp_path / "f", tmp_path / "blobs" / "name"
    source.write_bytes(BODY)
    blob.parent.mkdir()
    blob.write_bytes(BODY)
    before = blob.stat().st_ino
    assert hfstore.into_blobs(source, blob, hashlib.sha256(BODY).hexdigest()) == "have"
    assert blob.stat().st_ino == before


def test_a_blob_of_that_name_with_other_bytes_is_never_replaced(tmp_path):
    source, blob = tmp_path / "f", tmp_path / "blobs" / "name"
    source.write_bytes(BODY)
    blob.parent.mkdir()
    blob.write_bytes(b"x" * len(BODY))
    got = hfstore.into_blobs(source, blob, hashlib.sha256(BODY).hexdigest())
    assert "other bytes" in got and blob.read_bytes() == b"x" * len(BODY)


def test_another_volume_is_refused_when_a_copy_is_not_allowed(tmp_path, monkeypatch):
    source, blob = tmp_path / "f", tmp_path / "blobs" / "name"
    source.write_bytes(BODY)

    def across(*_args, **_kw):
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", across)
    got = hfstore.into_blobs(source, blob, hashlib.sha256(BODY).hexdigest(), copy=False)
    assert "another volume" in got and not blob.exists()
    assert not list(tmp_path.rglob("*.incomplete"))


def test_another_volume_copies_when_a_copy_is_allowed(tmp_path, monkeypatch):
    source, blob = tmp_path / "f", tmp_path / "blobs" / "name"
    source.write_bytes(BODY)
    real = os.link

    def across(src, dst, *args, **kw):
        if str(src) == str(source):
            raise OSError(errno.EXDEV, "cross-device link")
        return real(src, dst, *args, **kw)

    monkeypatch.setattr(os, "link", across)
    assert hfstore.into_blobs(source, blob, hashlib.sha256(BODY).hexdigest()) == "new"
    assert blob.read_bytes() == BODY


@pytest.mark.parametrize("name", ["not-a-digest", "a" * 63])
def test_a_name_that_is_not_a_digest_is_refused(tmp_path, name):
    source = tmp_path / "f"
    source.write_bytes(BODY)
    with pytest.raises(ValueError, match="digest"):
        hfstore.into_blobs(source, tmp_path / "blobs" / name, name)

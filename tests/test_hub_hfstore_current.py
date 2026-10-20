"""Actual no-replace publication retains only owned paths through cancellation/races."""
import errno
import hashlib
import os

import pytest

from poolhouse.hub import hfstore

BODY = b'checked model bytes' * 2048


def paths(tmp_path):
    source, target = tmp_path / 'source', tmp_path / 'models' / 'model.gguf'
    source.write_bytes(BODY)
    return source, target, hashlib.sha256(BODY).hexdigest()


def test_wrong_source_digest_never_links_even_on_same_volume(tmp_path):
    source, target, _ = paths(tmp_path)
    result = hfstore.into_blobs(source, target, 'a' * 64, current=lambda: True)
    assert 'did not hash' in result
    assert not target.exists() and source.read_bytes() == BODY


@pytest.mark.parametrize('value', [False, 1, None])
def test_current_requires_exact_true_before_directory_creation(tmp_path, value):
    source, target, expected = paths(tmp_path)
    with pytest.raises(PermissionError):
        hfstore.into_blobs(source, target, expected, current=lambda: value)
    assert not target.parent.exists()


@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_source_must_be_regular_without_following_final_symlink(tmp_path, kind):
    source, target, expected = paths(tmp_path)
    unsafe = tmp_path / 'unsafe'
    if kind == 'symlink':
        unsafe.symlink_to(source)
    else:
        os.mkfifo(unsafe)
    with pytest.raises(OSError):
        hfstore.into_blobs(unsafe, target, expected, current=lambda: True)
    assert unsafe.exists() and not target.exists()


@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_existing_unsafe_target_is_preserved_without_reading_it(tmp_path, kind):
    source, target, expected = paths(tmp_path)
    target.parent.mkdir()
    if kind == 'symlink':
        target.symlink_to(source)
    else:
        os.mkfifo(target)
    before = target.lstat().st_ino
    with pytest.raises(OSError):
        hfstore.into_blobs(source, target, expected, current=lambda: True)
    assert target.lstat().st_ino == before and source.read_bytes() == BODY


def test_revocation_after_link_removes_only_new_target(tmp_path, monkeypatch):
    source, target, expected = paths(tmp_path)
    allowed = [True]
    original = os.link

    def link(*args, **kwargs):
        original(*args, **kwargs)
        allowed[0] = False

    monkeypatch.setattr(os, 'link', link)
    with pytest.raises(PermissionError):
        hfstore.into_blobs(source, target, expected, current=lambda: allowed[0])
    assert not target.exists() and source.read_bytes() == BODY


def test_equal_byte_replacement_after_link_is_not_adopted_or_removed(tmp_path, monkeypatch):
    source, target, expected = paths(tmp_path)
    original = os.link
    replaced = []

    def link(*args, **kwargs):
        original(*args, **kwargs)
        target.unlink()
        target.write_bytes(BODY)
        replaced.append(target.stat().st_ino)

    monkeypatch.setattr(os, 'link', link)
    with pytest.raises(PermissionError, match='target changed'):
        hfstore.into_blobs(source, target, expected, current=lambda: True)
    assert target.stat().st_ino == replaced[0] and target.read_bytes() == BODY
    assert source.read_bytes() == BODY


def test_mutation_after_link_is_detected_by_published_descriptor_digest(tmp_path, monkeypatch):
    source, target, expected = paths(tmp_path)
    original = os.link

    def link(*args, **kwargs):
        original(*args, **kwargs)
        target.write_bytes(b'x' * len(BODY))

    monkeypatch.setattr(os, 'link', link)
    with pytest.raises(PermissionError, match='bytes changed'):
        hfstore.into_blobs(source, target, expected, current=lambda: True)
    assert not target.exists() and source.exists()


def test_existing_matching_data_never_becomes_owned_cleanup(tmp_path):
    source, target, expected = paths(tmp_path)
    target.parent.mkdir()
    target.write_bytes(BODY)
    before = target.stat().st_ino
    assert hfstore.into_blobs(source, target, expected, current=lambda: True) == 'have'
    with pytest.raises(PermissionError):
        hfstore.into_blobs(source, target, expected, current=lambda: False)
    assert target.stat().st_ino == before and target.read_bytes() == BODY


def test_copy_revocation_cleans_only_own_stage(tmp_path, monkeypatch):
    source, target, expected = paths(tmp_path)
    allowed, copying = [True], [False]
    original_read = os.read

    def link(*args, **kwargs):
        copying[0] = True
        raise OSError(errno.EXDEV, 'cross volume')

    def read(*args):
        block = original_read(*args)
        if copying[0]:
            allowed[0] = False
        return block

    monkeypatch.setattr(os, 'link', link)
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(PermissionError):
        hfstore.into_blobs(source, target, expected, current=lambda: allowed[0])
    assert not target.exists() and not list(target.parent.glob('*.incomplete'))
    assert source.read_bytes() == BODY


def test_replaced_copy_stage_is_preserved(tmp_path, monkeypatch):
    source, target, expected = paths(tmp_path)
    original = os.link
    foreign = []

    def link(src, dst, **kwargs):
        if str(src) == str(source):
            raise OSError(errno.EXDEV, 'cross volume')
        part = target.parent / str(src)
        part.unlink()
        part.write_bytes(BODY)
        foreign.append((part, part.stat().st_ino))
        return original(src, dst, **kwargs)

    monkeypatch.setattr(os, 'link', link)
    with pytest.raises(PermissionError, match='target changed'):
        hfstore.into_blobs(source, target, expected, current=lambda: True)
    assert foreign[0][0].stat().st_ino == foreign[0][1]
    assert source.read_bytes() == BODY


def test_redirected_publication_parent_never_creates_destination(tmp_path):
    source, _, expected = paths(tmp_path)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError):
        hfstore.into_blobs(source, alias / 'nested' / 'model.gguf', expected, current=lambda: True)
    assert list(elsewhere.iterdir()) == []


def test_changed_ancestor_during_publication_preserves_replacement(tmp_path, monkeypatch):
    source, target, expected = paths(tmp_path)
    original = os.link
    moved = tmp_path / 'moved-models'

    def link(*args, **kwargs):
        original(*args, **kwargs)
        target.parent.rename(moved)
        target.parent.mkdir()
        target.write_bytes(b'foreign')

    monkeypatch.setattr(os, 'link', link)
    with pytest.raises(PermissionError, match='directory changed'):
        hfstore.into_blobs(source, target, expected, current=lambda: True)
    assert target.read_bytes() == b'foreign'
    assert not (moved / target.name).exists()
    assert source.read_bytes() == BODY

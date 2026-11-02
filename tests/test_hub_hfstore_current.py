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
    with hfstore.Placement(source, target, 'a' * 64, current=lambda: True) as placement:
        result = placement.publish()
        if result in ("new", "have"):
            placement.commit()
    assert 'did not hash' in result
    assert not target.exists() and source.read_bytes() == BODY


@pytest.mark.parametrize('value', [False, 1, None])
def test_current_requires_exact_true_before_directory_creation(tmp_path, value):
    source, target, expected = paths(tmp_path)
    with pytest.raises(PermissionError), hfstore.Placement(source, target, expected, current=lambda: value) as placement:
        placement.publish()
        placement.commit()
    assert not target.parent.exists()


@pytest.mark.parametrize('kind', ['symlink', 'fifo'])
def test_source_must_be_regular_without_following_final_symlink(tmp_path, kind):
    source, target, expected = paths(tmp_path)
    unsafe = tmp_path / 'unsafe'
    if kind == 'symlink':
        unsafe.symlink_to(source)
    else:
        os.mkfifo(unsafe)
    with pytest.raises(OSError), hfstore.Placement(unsafe, target, expected, current=lambda: True) as placement:
        placement.publish()
        placement.commit()
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
    with pytest.raises(OSError), hfstore.Placement(source, target, expected, current=lambda: True) as placement:
        placement.publish()
        placement.commit()
    assert target.lstat().st_ino == before and source.read_bytes() == BODY


def test_revocation_after_link_removes_only_new_target(tmp_path, monkeypatch):
    source, target, expected = paths(tmp_path)
    allowed = [True]
    original = os.link

    def link(*args, **kwargs):
        original(*args, **kwargs)
        allowed[0] = False

    monkeypatch.setattr(os, 'link', link)
    with pytest.raises(PermissionError), hfstore.Placement(source, target, expected, current=lambda: allowed[0]) as placement:
        placement.publish()
        placement.commit()
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
    with pytest.raises(PermissionError, match='target changed'), hfstore.Placement(source, target, expected, current=lambda: True) as placement:
        placement.publish()
        placement.commit()
    assert target.stat().st_ino == replaced[0] and target.read_bytes() == BODY
    assert source.read_bytes() == BODY


def test_mutation_after_link_is_detected_by_published_descriptor_digest(tmp_path, monkeypatch):
    source, target, expected = paths(tmp_path)
    original = os.link

    def link(*args, **kwargs):
        original(*args, **kwargs)
        target.write_bytes(b'x' * len(BODY))

    monkeypatch.setattr(os, 'link', link)
    with pytest.raises(PermissionError, match='bytes changed'), hfstore.Placement(source, target, expected, current=lambda: True) as placement:
        placement.publish()
        placement.commit()
    assert not target.exists() and source.exists()


def test_existing_matching_data_never_becomes_owned_cleanup(tmp_path):
    source, target, expected = paths(tmp_path)
    target.parent.mkdir()
    target.write_bytes(BODY)
    before = target.stat().st_ino
    with hfstore.Placement(source, target, expected, current=lambda: True) as placement:
        assert placement.publish() == 'have'
        placement.commit()
    with pytest.raises(PermissionError), hfstore.Placement(source, target, expected, current=lambda: False) as placement:
        placement.publish()
        placement.commit()
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
    with pytest.raises(PermissionError), hfstore.Placement(source, target, expected, current=lambda: allowed[0]) as placement:
        placement.publish()
        placement.commit()
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
    with pytest.raises(PermissionError, match='target changed'), hfstore.Placement(source, target, expected, current=lambda: True) as placement:
        placement.publish()
        placement.commit()
    assert foreign[0][0].stat().st_ino == foreign[0][1]
    assert source.read_bytes() == BODY


def test_redirected_publication_parent_never_creates_destination(tmp_path):
    source, _, expected = paths(tmp_path)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    alias = tmp_path / 'alias'
    alias.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(OSError), hfstore.Placement(source, alias / 'nested' / 'model.gguf', expected, current=lambda: True) as placement:
        placement.publish()
        placement.commit()
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
    with pytest.raises(PermissionError, match='directory changed'), hfstore.Placement(source, target, expected, current=lambda: True) as placement:
        placement.publish()
        placement.commit()
    assert target.read_bytes() == b'foreign'
    assert not (moved / target.name).exists()
    assert source.read_bytes() == BODY


def test_caller_exception_cleans_uncommitted_owned_publication(tmp_path):
    source, target, expected = paths(tmp_path)
    with pytest.raises(RuntimeError, match='caller'), hfstore.Placement(source, target, expected) as placement:
        assert placement.publish() == 'new'
        raise RuntimeError('caller validation failed')
    assert not target.exists() and source.read_bytes() == BODY


def test_replacement_during_caller_validation_is_preserved_not_committed(tmp_path):
    source, target, expected = paths(tmp_path)
    with hfstore.Placement(source, target, expected) as placement:
        assert placement.publish() == 'new'
        original = target.stat().st_ino
        target.unlink()
        target.write_bytes(BODY)
        replacement = target.stat().st_ino
        assert replacement != original
        with pytest.raises(PermissionError, match='target changed'):
            placement.commit()
    assert target.stat().st_ino == replacement and target.read_bytes() == BODY


def test_current_denial_during_commit_removes_only_owned_publication(tmp_path):
    source, target, expected = paths(tmp_path)
    allowed = [True]
    with hfstore.Placement(source, target, expected, current=lambda: allowed[0]) as placement:
        assert placement.publish() == 'new'
        allowed[0] = False
        with pytest.raises(PermissionError):
            placement.commit()
    assert not target.exists() and source.exists()


def test_uncommitted_have_never_owns_existing_matching_file(tmp_path):
    source, target, expected = paths(tmp_path)
    target.parent.mkdir()
    target.write_bytes(BODY)
    original = target.stat().st_ino
    with hfstore.Placement(source, target, expected) as placement:
        assert placement.publish() == 'have'
    assert target.stat().st_ino == original and target.read_bytes() == BODY


def test_publication_and_commit_repeat_without_copy_or_adoption(tmp_path):
    source, target, expected = paths(tmp_path)
    with hfstore.Placement(source, target, expected) as placement:
        assert placement.publish() == placement.publish() == 'new'
        original = placement.commit()
        assert placement.commit() == original
    placement.__exit__(None, None, None)
    assert target.stat().st_ino == original.st_ino
    with pytest.raises(PermissionError, match='closed'):
        placement.commit()


def test_failed_entry_is_terminal_before_closed_descriptor_can_be_reused(tmp_path):
    source, target, expected = paths(tmp_path)
    unsafe = tmp_path / 'unsafe'
    os.mkfifo(unsafe)
    placement = hfstore.Placement(unsafe, target, expected)
    with pytest.raises(PermissionError):
        placement.__enter__()
    placement.__exit__(None, None, None)
    with pytest.raises(PermissionError, match='closed'):
        placement.publish()
    with pytest.raises(PermissionError, match='closed'):
        placement.commit()
    assert not target.exists() and source.read_bytes() == BODY

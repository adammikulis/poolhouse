"""Warm checkpoints reuse strong checksums without changing artifact identity or authority."""
import hashlib
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from poolhouse.studio import model_content
from poolhouse.studio.engine import artifact_fingerprints
from poolhouse.studio.resources import base_fingerprint
from poolhouse.studio.store import digest


def original_digest(path):
    files = sorted(path.rglob('*')) if path.is_dir() else [path]
    root = path if path.is_dir() else path.parent
    return digest([[str(file.relative_to(root)), file.stat().st_size, hashlib.sha256(file.read_bytes()).hexdigest()]
                   if file.is_file() else [str(file.relative_to(root)), 'directory'] for file in files])


@pytest.fixture
def checkpoint(tmp_path):
    folder = tmp_path / 'checkpoint'
    folder.mkdir()
    (folder / 'weights').write_bytes(b'fresh weights')
    (folder / 'nested').mkdir()
    (folder / 'empty').mkdir()
    (folder / 'nested' / 'config.json').write_text('{}')
    return {'kind': 'model', 'path': str(folder)}


@pytest.mark.parametrize('single', [False, True])
def test_existing_file_and_directory_framing_unchanged_and_warm_content_not_reopened(checkpoint, monkeypatch, single):
    if single:
        checkpoint['path'] = str(Path(checkpoint['path']) / 'weights')
    path = Path(checkpoint['path'])
    expected = {str(path): original_digest(path)}
    opened = []
    original = model_content.open_model_file
    @contextmanager
    def counted(member):
        opened.append(member['path'])
        with original(member) as source:
            yield source
    monkeypatch.setattr(model_content, 'open_model_file', counted)
    assert artifact_fingerprints(checkpoint) == expected
    count = len(opened)
    assert count == (1 if single else 2)
    assert artifact_fingerprints(checkpoint) == expected
    assert len(opened) == count


def test_ctime_changed_same_size_and_restored_mtime_invalidates(checkpoint):
    path = Path(checkpoint['path']) / 'weights'
    before = artifact_fingerprints(checkpoint)
    info = path.stat()
    path.write_bytes(b'other weights')
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert path.stat().st_size == info.st_size
    assert artifact_fingerprints(checkpoint) != before


def test_renames_and_empty_directories_remain_part_of_identity(checkpoint):
    root = Path(checkpoint['path'])
    first = artifact_fingerprints(checkpoint)
    (root / 'empty').rename(root / 'renamed-empty')
    second = artifact_fingerprints(checkpoint)
    assert first != second
    (root / 'weights').rename(root / 'renamed-weights')
    assert artifact_fingerprints(checkpoint) != second


@pytest.mark.parametrize('attack', ['file-link', 'directory-link', 'ancestor-link', 'hardlink'])
def test_warm_cache_never_skips_fresh_link_checks(checkpoint, tmp_path, attack):
    root = Path(checkpoint['path'])
    artifact_fingerprints(checkpoint)
    if attack == 'ancestor-link':
        alias = tmp_path / 'alias'
        alias.symlink_to(root, target_is_directory=True)
        checkpoint['path'] = str(alias / 'nested')
    elif attack == 'hardlink':
        os.link(root / 'weights', tmp_path / 'other-name')
    else:
        original = root / ('weights' if attack == 'file-link' else 'nested')
        moved = tmp_path / 'outside'
        original.rename(moved)
        original.symlink_to(moved, target_is_directory=moved.is_dir())
    with pytest.raises(ValueError, match=r'link|directory'):
        artifact_fingerprints(checkpoint)


def test_current_base_proof_is_checked_even_when_checkpoint_hash_is_warm(checkpoint, tmp_path):
    base = tmp_path / 'base'
    base.mkdir()
    (base / 'weights').write_bytes(b'base')
    checkpoint.update(base={'kind': 'model', 'path': str(base)})
    checkpoint['base_digest'] = base_fingerprint(checkpoint['base'])
    artifact_fingerprints(checkpoint)
    (base / 'weights').write_bytes(b'evil')
    with pytest.raises(ValueError, match='base content changed'):
        artifact_fingerprints(checkpoint)


def test_mutation_during_initial_hash_is_not_published_to_cache(checkpoint, monkeypatch):
    original = model_content.open_model_file
    @contextmanager
    def changed(member):
        with original(member) as source:
            yield source
            Path(member['path']).write_bytes(b'changed after read')
    before = model_content._fingerprint_checked.cache_info().currsize
    with monkeypatch.context() as patch:
        patch.setattr(model_content, 'open_model_file', changed)
        with pytest.raises(ValueError, match='changed'):
            artifact_fingerprints(checkpoint)
    assert model_content._fingerprint_checked.cache_info().currsize == before
    assert artifact_fingerprints(checkpoint) == {checkpoint['path']: original_digest(Path(checkpoint['path']))}

"""Installed model links are trusted only within their exact native cache repository."""
import hashlib
from pathlib import Path

import pytest

from poolhouse.hub import ModelInfo
from poolhouse.studio import resources


@pytest.fixture
def cached_model(tmp_path, monkeypatch):
    cache = tmp_path / 'cache'
    repo = cache / 'models--sample--model'
    snapshot, blobs = repo / 'snapshots' / ('a' * 40), repo / 'blobs'
    snapshot.mkdir(parents=True)
    blobs.mkdir()
    weights = blobs / hashlib.sha256(b'weights').hexdigest()
    weights.write_bytes(b'weights')
    (snapshot / 'model.safetensors').symlink_to(Path('../../blobs') / weights.name)
    (snapshot / 'config.json').write_text('{"model_type":"sample"}')
    entry = ModelInfo('hf:sample/model', 'model', snapshot, 'mlx', 7, 'huggingface', repo='sample/model')
    monkeypatch.setattr(resources.hub, 'discover', lambda: [entry])
    monkeypatch.setattr(resources.hub, 'hub_cache', lambda: cache)
    return resources.model_resource({'kind': 'model', 'id': entry.id}), snapshot, blobs


def test_native_snapshot_fingerprint_uses_exact_blob_bytes(cached_model):
    handle, snapshot, blobs = cached_model
    members = resources.model_files(handle)
    weights = next(member for member in members if member['relative'] == 'model.safetensors')
    assert Path(weights['path']).parent == blobs
    with resources.open_model_file(weights) as source:
        assert source.read() == b'weights'
    first = resources.model_fingerprint(handle)
    Path(weights['path']).write_bytes(b'changed')
    assert resources.model_fingerprint(handle) != first
    assert (snapshot / 'model.safetensors').is_symlink()


@pytest.mark.parametrize('escape', ['cross-repo', 'outside-blob', 'blob-chain', 'directory'])
def test_model_links_cannot_nominate_other_cache_or_private_paths(cached_model, tmp_path, escape):
    handle, snapshot, blobs = cached_model
    link = snapshot / 'model.safetensors'
    link.unlink()
    private = tmp_path / 'private'
    private.write_bytes(b'private')
    if escape == 'cross-repo':
        target = blobs.parent.parent / 'models--other--model/blobs/private'
        target.parent.mkdir(parents=True)
        target.write_bytes(b'private')
    elif escape == 'outside-blob':
        target = private
    elif escape == 'blob-chain':
        target = blobs / 'linked'
        target.symlink_to(private)
    else:
        target = snapshot / 'nested'
        target.mkdir()
    link.symlink_to(target)
    with pytest.raises(ValueError, match='link'):
        resources.model_files(handle)


def test_model_descriptor_replacement_before_open_is_refused(cached_model, tmp_path):
    handle, _, _ = cached_model
    member = next(member for member in resources.model_files(handle) if member['relative'] == 'model.safetensors')
    private = tmp_path / 'private'
    private.write_bytes(b'private')
    path = Path(member['path'])
    path.unlink()
    path.symlink_to(private)
    with pytest.raises(ValueError, match='changed'), resources.open_model_file(member):
        pytest.fail('a changed descriptor must not be readable')


def test_forged_model_handle_cannot_retarget_a_known_id(cached_model, tmp_path):
    handle, _, _ = cached_model
    with pytest.raises(ValueError, match='directory changed'):
        resources.model_files({**handle, 'path': str(tmp_path)})
    with pytest.raises(ValueError, match='model library'):
        resources.model_files({**handle, 'id': 'https://injected.invalid/model'})


def test_original_snapshot_directory_link_cannot_disappear_during_resolution(cached_model, tmp_path, monkeypatch):
    handle, snapshot, _ = cached_model
    alias = tmp_path / 'aliased-cache'
    alias.symlink_to(snapshot.parent, target_is_directory=True)
    entry = ModelInfo(handle['id'], 'model', alias / snapshot.name, 'mlx', 7, 'huggingface', repo='sample/model')
    monkeypatch.setattr(resources.hub, 'discover', lambda: [entry])
    with pytest.raises(ValueError, match='directory cannot be a symbolic link'):
        resources.model_resource({'kind': 'model', 'id': entry.id})


def test_original_repository_directory_link_cannot_authorize_blob_targets(cached_model, tmp_path, monkeypatch):
    handle, _, blobs = cached_model
    alias = tmp_path / 'aliased-repository'
    alias.symlink_to(blobs.parent, target_is_directory=True)
    monkeypatch.setattr(resources.hfstore, 'repo_root', lambda repo: alias)
    with pytest.raises(ValueError, match='cache directory cannot be a symbolic link'):
        resources.model_files(handle)

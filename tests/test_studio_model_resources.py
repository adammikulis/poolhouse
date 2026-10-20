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


def test_materialized_base_identity_matches_native_snapshot_without_cache_lookup(cached_model, tmp_path, monkeypatch):
    handle, _, _ = cached_model
    staged = tmp_path / 'staged'
    staged.mkdir()
    for member in resources.model_files(handle):
        target = staged / member['relative']
        target.parent.mkdir(parents=True, exist_ok=True)
        with resources.open_model_file(member) as source:
            target.write_bytes(source.read())
    expected = resources.model_fingerprint(handle)
    monkeypatch.setattr(resources.hub, 'discover', lambda: pytest.fail('staged base must not query ambient model cache'))
    assert resources.base_fingerprint({'kind': 'model', 'path': str(staged)}) == expected
    private = tmp_path / 'private'
    private.write_bytes(b'private')
    (staged / 'injected').symlink_to(private)
    with pytest.raises(ValueError, match='symbolic links'):
        resources.base_fingerprint({'kind': 'model', 'path': str(staged)})


@pytest.fixture
def shared_model(cached_model):
    handle, _, blobs = cached_model
    original = next(blobs.iterdir())
    digest = 'b' * 64  # Shared storage locators are independent of repository content digests.
    shared = blobs.parent.parent / 'blobs' / digest[:2] / digest
    shared.parent.mkdir(parents=True)
    original.replace(shared)
    original.symlink_to(Path('../../blobs') / digest[:2] / digest)
    return handle, original, shared


def test_shared_content_addressed_native_blob(shared_model):
    handle, _, shared = shared_model
    member = next(m for m in resources.model_files(handle) if m['relative'] == 'model.safetensors')
    assert member['path'] == str(shared)
    assert member['blob_digest'] == hashlib.sha256(b'weights').hexdigest()
    assert member['blob_digest'] != shared.name
    assert resources.model_fingerprint(handle)


@pytest.mark.parametrize('attack', ['wrong-shard', 'nonhex', 'extra-hop', 'outside', 'ancestor', 'changed-bytes'])
def test_shared_blob_boundary(shared_model, tmp_path, attack):
    handle, original, shared = shared_model
    if attack == 'changed-bytes':
        shared.write_bytes(b'forged')
    elif attack == 'ancestor':
        shard = shared.parent
        moved = shard.with_name('moved')
        shard.rename(moved)
        shard.symlink_to(moved, target_is_directory=True)
    else:
        original.unlink()
        if attack == 'wrong-shard':
            target = shared.parent.parent / 'ff' / shared.name
        elif attack == 'nonhex':
            target = shared.with_name('invalid')
        elif attack == 'extra-hop':
            target = shared
            shared.unlink()
            shared.symlink_to(tmp_path / 'private')
        else:
            target = tmp_path / 'private'
        original.symlink_to(target)
    with pytest.raises((ValueError, FileNotFoundError)):
        resources.model_fingerprint(handle)


def test_shared_blob_replacement_before_open_is_refused(shared_model, tmp_path):
    handle, _, shared = shared_model
    member = next(m for m in resources.model_files(handle) if m['relative'] == 'model.safetensors')
    shared.unlink()
    shared.symlink_to(tmp_path / 'private')
    with pytest.raises(ValueError, match='changed'), resources.open_model_file(member):
        pytest.fail('replaced shared blob must not be read')


def test_shared_blob_resume_hashes_skipped_upload_prefix(shared_model):
    handle, _, _ = shared_model
    member = next(m for m in resources.model_files(handle) if m['relative'] == 'model.safetensors')
    with resources.open_model_file(member) as source:
        assert source.read(2) == b'we'
        assert source.seek(5) == 5
        assert source.read() == b'ts'
    with resources.open_model_file(member) as source:
        source.read(2)
        with pytest.raises(ValueError, match='forward'):
            source.seek(0)
    with pytest.raises(ValueError, match='exceeds'), resources.open_model_file(member) as source:
        source.seek(member['size'] + 1)


@pytest.mark.parametrize('changed', [False, True])
def test_shared_blob_uses_repository_git_object_digest(shared_model, changed):
    handle, original, shared = shared_model
    snapshot = Path(handle['path'])
    oid = hashlib.sha1(b'blob 7\0weights', usedforsecurity=False).hexdigest()
    git_blob = original.with_name(oid)
    original.rename(git_blob)
    link = snapshot / 'model.safetensors'
    link.unlink()
    link.symlink_to(Path('../../blobs') / oid)
    if changed:
        shared.write_bytes(b'forged!')
        with pytest.raises(ValueError, match='content digest'):
            resources.model_fingerprint(handle)
    else:
        assert resources.model_fingerprint(handle)


def test_shared_blob_requires_valid_repository_content_digest(shared_model):
    handle, original, _ = shared_model
    invalid = original.with_name('not-a-content-digest')
    original.rename(invalid)
    link = Path(handle['path']) / 'model.safetensors'
    link.unlink()
    link.symlink_to(Path('../../blobs') / invalid.name)
    with pytest.raises(ValueError, match='valid content digest'):
        resources.model_files(handle)

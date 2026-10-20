"""Dataset files stay beneath descriptor-anchored managed roots and match their manifest."""
import json
import os

import pytest

from poolhouse.studio import dataset_assets as assets, dataset_samples as samples


@pytest.fixture
def content(tmp_path):
    preview = {'columns': [{'name': 'text', 'type': 'string'}], 'rows': [{'text': 'row'}]}
    body = {'repo': 'example/data', 'config': 'default', 'split': 'train', 'name': 'Selected row',
        'idempotency_key': 'once', 'row_indexes': [0], 'columns': {'text': 'text'},
        'expected_preview_digest': samples.preview_digest('example/data', 'default', 'train', preview)}
    data, manifest = samples.selected(body, preview)
    return tmp_path, data, manifest


def test_publish_is_idempotent_and_private(content):
    root, data, manifest = content
    relative = 'datasets/huggingface/test'
    path = assets.publish(root, relative, data, manifest)
    assert assets.publish(root, relative, data, manifest) == path
    assert path.joinpath('dataset.jsonl').read_bytes() == data
    assert path.joinpath('dataset.jsonl').stat().st_mode & 0o777 == 0o600
    with assets.folder(root, relative) as directory:
        assert assets.verify(directory, manifest) == (data, manifest)
    assert not list(path.glob('*.part'))


@pytest.mark.parametrize('relative', ['../outside', '/tmp/outside'])
def test_escaping_output_refused(content, relative):
    root, data, manifest = content
    with pytest.raises(ValueError):
        assets.publish(root, relative, data, manifest)


def test_parent_link_does_not_create_outside(content, tmp_path):
    root, data, manifest = content
    outside = tmp_path / 'outside'
    outside.mkdir()
    (root / 'datasets').symlink_to(outside)
    with pytest.raises(OSError):
        assets.publish(root, 'datasets/not-created', data, manifest)
    assert not (outside / 'not-created').exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'changed'])
def test_replaced_asset_refused(content, kind):
    root, data, manifest = content
    path = assets.publish(root, 'dataset', data, manifest)
    target = path / 'dataset.jsonl'
    if kind == 'symlink':
        target.unlink()
        target.symlink_to(root / 'outside')
    elif kind == 'hardlink':
        os.link(target, root / 'second')
    else:
        target.write_bytes(b'{"text":"changed"}\n')
    with pytest.raises((ValueError, OSError)), assets.folder(root, 'dataset') as directory:
        assets.verify(directory, manifest)


def test_modified_manifest_cannot_change_expected_identity(content):
    root, data, manifest = content
    path = assets.publish(root, 'dataset', data, manifest)
    modified = dict(manifest, rows=20)
    (path / 'dataset-import.json').write_text(json.dumps(modified))
    with pytest.raises(ValueError), assets.folder(root, 'dataset') as directory:
        assets.verify(directory, manifest)


def test_mid_read_replacement_denied(content, monkeypatch):
    root, data, manifest = content
    path = assets.publish(root, 'dataset', data, manifest)
    original = assets.os.stat
    changed = False
    def replace(name, *args, **kwargs):
        nonlocal changed
        if name == 'dataset.jsonl' and not changed:
            changed = True
            replacement = path / 'replacement'
            replacement.write_bytes(data)
            replacement.replace(path / name)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(assets.os, 'stat', replace)
    with pytest.raises(ValueError, match='changed'), assets.folder(root, 'dataset') as directory:
        assets.verify(directory, manifest)


@pytest.mark.parametrize('attack', ['target', 'part'])
def test_promotion_preserves_concurrent_unowned_files(content, monkeypatch, attack):
    root, data, manifest = content
    original = assets.os.link
    replacement = b'concurrent independent content'
    def race(source, target, **kwargs):
        descriptor = kwargs['src_dir_fd']
        name = target if attack == 'target' else source
        if attack == 'part':
            os.unlink(source, dir_fd=descriptor)
        handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=descriptor)
        with os.fdopen(handle, 'wb') as file:
            file.write(replacement)
        return original(source, target, **kwargs)
    monkeypatch.setattr(assets.os, 'link', race)
    with pytest.raises((ValueError, OSError)):
        assets.publish(root, 'dataset', data, manifest)
    path = root / 'dataset'
    assert any(file.read_bytes() == replacement for file in path.iterdir())
    assert not (path / 'dataset-import.json').exists()


@pytest.mark.parametrize('point', ['linked-read', 'final-read'])
def test_promotion_rejects_equal_bytes_from_replacement_inode(content, monkeypatch, point):
    root, data, manifest = content
    changed = False
    linked, read = assets.read_linked, assets.read
    def replace(directory, name):
        nonlocal changed
        if changed or name != 'dataset.jsonl':
            return
        changed = True
        os.unlink(name, dir_fd=directory)
        handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        with os.fdopen(handle, 'wb') as output:
            output.write(data)
    def replace_linked(directory, name, raw, identity):
        if point == 'linked-read':
            replace(directory, name)
        return linked(directory, name, raw, identity)
    def replace_final(directory, name, **kwargs):
        if point == 'final-read' and kwargs.get('identity') is not None:
            replace(directory, name)
        return read(directory, name, **kwargs)
    monkeypatch.setattr(assets, 'read_linked', replace_linked)
    monkeypatch.setattr(assets, 'read', replace_final)
    with pytest.raises(ValueError):
        assets.publish(root, 'dataset', data, manifest)
    assert changed and (root / 'dataset' / 'dataset.jsonl').read_bytes() == data
    assert not (root / 'dataset' / 'dataset-import.json').exists()

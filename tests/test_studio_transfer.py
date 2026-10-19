"""Authenticated resource staging integrity and containment."""
import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest

from poolhouse.fleet.studio_transfer import CHUNK_BYTES, TransferStore, checked_manifest, manifest
from poolhouse.studio.catalog import node_spec

DISPATCH = 'a' * 64
SOURCE = 'enrolled-source-certificate'


@pytest.fixture
def transfer(tmp_path):
    live = {'allowed': True}
    store = TransferStore(tmp_path / 'target', max_bytes=1024 * 1024,
                          authorize=lambda source, dispatch: source == SOURCE and live['allowed'])
    source = tmp_path / 'source.txt'
    source.write_bytes(b'known resource bytes')
    return store, source, live


def stage(store, source):
    value = manifest(source, 'dataset')
    store.begin(SOURCE, DISPATCH, value)
    store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=source.read_bytes())
    return value


def resign(value):
    value.pop('id', None)
    value['id'] = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return value


def test_exact_chunks_finalize_and_restarts_are_idempotent(transfer):
    store, source, _ = transfer
    value = manifest(source, 'dataset')
    assert store.begin(SOURCE, DISPATCH, value) == store.begin(SOURCE, DISPATCH, value)
    data = source.read_bytes()
    first = store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=data)
    assert store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=data) == first
    with pytest.raises(FileNotFoundError):
        store.read(SOURCE, DISPATCH, value['id'], name=source.name)
    assert store.finish(SOURCE, DISPATCH, value['id']) == store.finish(SOURCE, DISPATCH, value['id']) == value
    assert store.read(SOURCE, DISPATCH, value['id'], name=source.name) == data
    assert store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=data) == first
    reopened = TransferStore(store.root, max_bytes=store.max_bytes, authorize=store.authorize)
    assert reopened.finish(SOURCE, DISPATCH, value['id']) == value
    hydrated = reopened.inputs(SOURCE, DISPATCH, {'dataset': {'kind': 'dataset', 'resource': value['id'], 'rows': 2}})
    assert hydrated['dataset']['rows'] == 2
    assert source.read_bytes() == Path(hydrated['dataset']['path']).read_bytes()
    assert not any(path.name == 'incoming' for path in store.root.rglob('*'))


def test_truncated_tampered_or_changed_repeated_chunks_refused(transfer):
    store, source, _ = transfer
    value = manifest(source, 'dataset')
    store.begin(SOURCE, DISPATCH, value)
    store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=b'x')
    with pytest.raises(ValueError, match='content'):
        store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=b'y')
    with pytest.raises(ValueError, match='digest'):
        store.finish(SOURCE, DISPATCH, value['id'])
    with pytest.raises(ValueError, match='order'):
        store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=2, data=b'x')
    with pytest.raises(ValueError, match='outside'):
        store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=b'x' * 100)
    with pytest.raises(ValueError, match='chunk'):
        store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=b'x' * (CHUNK_BYTES + 1))


def test_revocation_between_chunks_finalize_and_fetch(transfer):
    store, source, live = transfer
    value = stage(store, source)
    live['allowed'] = False
    for operation in (lambda: store.finish(SOURCE, DISPATCH, value['id']),
                      lambda: store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=b''),
                      lambda: store.read(SOURCE, DISPATCH, value['id'], name=source.name),
                      lambda: store.inputs(SOURCE, DISPATCH, {})):
        with pytest.raises(PermissionError):
            operation()
    live['allowed'] = True
    with pytest.raises(PermissionError):
        store.finish('another-source', DISPATCH, value['id'])


@pytest.mark.parametrize('name', ['/outside', '../outside', 'x/../../outside', 'x\\outside', 'x//outside', './outside'])
def test_manifest_paths_are_strict_relative_names(transfer, name):
    _, source, _ = transfer
    value = manifest(source, 'dataset')
    value['files'][0]['name'] = name
    value['entrypoint'] = name
    resign(value)
    with pytest.raises(ValueError):
        checked_manifest(value, 1000)


def test_manifest_sizes_digests_and_total_reservation_are_checked(transfer):
    store, source, _ = transfer
    value = manifest(source, 'dataset')
    bad = deepcopy(value)
    bad['files'][0]['size'] = True
    resign(bad)
    with pytest.raises(ValueError):
        store.begin(SOURCE, DISPATCH, bad)
    bad = deepcopy(value)
    bad['id'] = '0' * 64
    with pytest.raises(ValueError, match='digest'):
        store.begin(SOURCE, DISPATCH, bad)
    small = TransferStore(store.root / 'small', max_bytes=value['bytes'], authorize=store.authorize)
    small.begin(SOURCE, DISPATCH, value)
    with pytest.raises(ValueError, match='space'):
        small.begin(SOURCE, 'b' * 64, value)


def test_source_and_staging_links_are_refused(transfer, tmp_path):
    store, source, _ = transfer
    linked = tmp_path / 'linked'
    linked.symlink_to(source)
    with pytest.raises(ValueError):
        manifest(linked, 'dataset')
    hard = tmp_path / 'hard'
    os.link(source, hard)
    with pytest.raises(ValueError):
        manifest(source, 'dataset')
    hard.unlink()
    value = manifest(source, 'dataset')
    store.begin(SOURCE, DISPATCH, value)
    parts = store._parts(SOURCE, DISPATCH, value['id'])
    (store.root.joinpath(*parts) / 'incoming').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=b'x')
    rootlink = tmp_path / 'rootlink'
    rootlink.symlink_to(store.root, target_is_directory=True)
    with pytest.raises(OSError):
        TransferStore(rootlink, max_bytes=100, authorize=store.authorize)


def test_target_outputs_cannot_reuse_inputs_or_other_dispatch_paths(transfer):
    store, source, _ = transfer
    value = stage(store, source)
    store.finish(SOURCE, DISPATCH, value['id'])
    data = store.inputs(SOURCE, DISPATCH, {'dataset': {'kind': 'dataset', 'resource': value['id']}})
    with pytest.raises(ValueError, match='belong'):
        store.result(SOURCE, DISPATCH, data, node_spec('data.validate'))


def test_raw_resource_paths_and_other_source_namespaces_are_unavailable(transfer):
    store, source, _ = transfer
    with pytest.raises(ValueError, match='staged'):
        store.inputs(SOURCE, DISPATCH, {'model': {'kind': 'model', 'path': str(source)}})
    value = stage(store, source)
    store.finish(SOURCE, DISPATCH, value['id'])
    store.authorize = lambda source, dispatch: True
    with pytest.raises(FileNotFoundError):
        store.read('other-authenticated-source', DISPATCH, value['id'], name=source.name)


def test_equal_length_tampered_resource_never_promotes(transfer):
    store, source, _ = transfer
    value = manifest(source, 'dataset')
    store.begin(SOURCE, DISPATCH, value)
    store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=b'x' * source.stat().st_size)
    with pytest.raises(ValueError, match='digest'):
        store.finish(SOURCE, DISPATCH, value['id'])
    assert not any(path.name == 'files' for path in store.root.rglob('*'))


def test_descriptor_promotion_remains_anchored_when_parent_path_is_replaced(tmp_path):
    from poolhouse.files import promote
    owned = tmp_path / 'owned'
    owned.mkdir()
    (owned / 'incoming').mkdir()
    (owned / 'incoming' / 'data').write_bytes(b'owned bytes')
    outside = tmp_path / 'outside'
    outside.mkdir()
    descriptor = os.open(owned, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        saved = tmp_path / 'saved'
        owned.rename(saved)
        owned.symlink_to(outside, target_is_directory=True)
        promote('incoming', 'files', directory_fd=descriptor)
        assert (saved / 'files' / 'data').read_bytes() == b'owned bytes'
        assert not list(outside.iterdir())
        for name in ('../outside', '/outside', 'nested/file'):
            with pytest.raises(ValueError):
                promote(name, 'file', directory_fd=descriptor)
    finally:
        os.close(descriptor)


def test_replayed_chunk_recovers_a_partial_write(transfer):
    store, source, _ = transfer
    value = manifest(source, 'dataset')
    store.begin(SOURCE, DISPATCH, value)
    data = source.read_bytes()
    store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=data[:3])
    assert store.put(SOURCE, DISPATCH, value['id'], name=source.name, offset=0, data=data) == {'offset': len(data)}
    assert store.finish(SOURCE, DISPATCH, value['id']) == value

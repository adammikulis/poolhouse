"""Completed training binds portable bases to actual server-resolved inputs."""

import json

import pytest

from poolhouse.studio.engine import artifact_fingerprints
from poolhouse.studio.model_lineage import bind_model_base
from poolhouse.studio.resources import base_fingerprint


@pytest.mark.parametrize("producer", ["train.tool-calls", "train.decision"])
def test_successful_producer_replaces_worker_base_and_writes_content_sidecar(tmp_path, producer):
    base = tmp_path / 'base'
    base.mkdir()
    (base / 'weights.safetensors').write_bytes(b'actual frozen base')
    output = tmp_path / 'output'
    model = output / 'model'
    model.mkdir(parents=True)
    (model / 'state.json').write_text('{}')
    handle = {'kind': 'model', 'path': str(base)}
    outputs = {'model': {'kind': 'model', 'path': str(model), 'base': {'path': '/private/forged'}}}
    bind_model_base({'type': producer}, outputs, {'model': handle}, output)
    assert outputs['model']['base'] == handle
    assert outputs['model']['base_digest'] == base_fingerprint(handle)
    assert json.loads((model / 'studio-model.json').read_text()) == {
        'version': 1, 'base_digest': base_fingerprint(handle)}
    bind_model_base({'type': producer}, outputs, {'model': handle}, output)
    assert outputs['model']['base'] == handle
    before = artifact_fingerprints(outputs['model'])
    assert str(base) in before
    (base / 'weights.safetensors').write_bytes(b'changed base')
    with pytest.raises(ValueError, match='base content changed'):
        artifact_fingerprints(outputs['model'])


def test_checkpoint_lineage_does_not_write_through_links_or_unowned_output(tmp_path):
    base = tmp_path / 'base'
    base.mkdir()
    (base / 'weights').write_bytes(b'base')
    model = tmp_path / 'model'
    model.mkdir()
    private = tmp_path / 'private'
    private.write_bytes(b'untouched')
    (model / 'studio-model.json').symlink_to(private)
    with pytest.raises(ValueError, match='regular sidecar'):
        bind_model_base({'type': 'train.tool-calls'}, {'model': {'path': str(model)}},
                        {'model': {'kind': 'model', 'path': str(base)}}, tmp_path)
    assert private.read_bytes() == b'untouched'
    with pytest.raises(ValueError, match='explicit authorized base'):
        bind_model_base({'type': 'train.tool-calls'}, {'model': {'path': str(model)}}, {}, tmp_path)

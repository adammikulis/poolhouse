"""Explicit checkpoint dependencies, inert metadata and declared tensor assets."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from poolhouse.studio.checkpoint_assets import prepare_checkpoint


def files(root):
    return [{'name': path.relative_to(root).as_posix(), 'size': path.stat().st_size,
             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(root.rglob('*')) if path.is_file()]


def identity(entries):
    rows = [[entry['name'], entry['size'], entry['sha256']] for entry in entries]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value))


@pytest.fixture
def assets(tmp_path):
    base, model = tmp_path / 'base', tmp_path / 'checkpoint'
    base.mkdir()
    model.mkdir()
    write_json(base / 'config.json', {'model_type': 'fixture'})
    write_json(base / 'tokenizer.json', {'version': 'fixture'})
    (base / 'model.safetensors').write_bytes(b'frozen base bytes')
    members = files(base)
    handle = {'kind': 'model', 'path': str(base), 'digest': identity(members), 'files': members}
    state = {'step': 1, 'config': {'recipe': 'tool-calls', 'framework': 'mlx', 'lora': True,
             'base': '/private/unavailable-base', 'base_id': 'not-a-cache-authority',
             'base_config': {'auto_map': {'Model': 'injected.module'}},
             'tokenizer_file': '/private/unavailable-tokenizer', 'trust_remote_code': True,
             'mlx_base_identity': {'files': {entry['name']: entry['sha256'] for entry in members}}},
             'extra': {'tokenizer_name_or_path': 'https://unrelated.invalid/model'}}
    write_json(model / 'state.json', state)
    write_json(model / 'studio-model.json', {'version': 1, 'base_digest': handle['digest']})
    (model / 'model.safetensors').write_bytes(b'trained model bytes')
    return model, handle, tmp_path / 'owned'


def prepare(assets):
    model, base, destination = assets
    return prepare_checkpoint(model, base, files(model), destination)


def test_explicit_base_matches_and_prepared_copy_is_inert_and_idempotent(assets):
    model, base, destination = assets
    write_json(model / 'tokenizer_config.json', {'tokenizer_file': '/private/not-an-authority'})
    (model / 'modeling_injected.py').write_text("raise RuntimeError('must remain inert')")
    original = {path.name: path.read_bytes() for path in model.iterdir()}
    first = prepare(assets)
    assert prepare(assets) == first
    assert first == {'path': str(destination), 'base': base, 'base_digest': base['digest']}
    assert {path.name: path.read_bytes() for path in model.iterdir()} == original
    config = json.loads((destination / 'state.json').read_text())['config']
    assert not set(config) & {'base', 'base_id', 'base_config', 'tokenizer_file', 'trust_remote_code'}
    assert config['mlx_base_identity'] == json.loads((model / 'state.json').read_text())['config']['mlx_base_identity']
    assert not (destination / 'tokenizer_config.json').exists()
    assert not (destination / 'modeling_injected.py').exists()
    assert (destination / 'model.safetensors').read_bytes() == b'trained model bytes'


@pytest.mark.parametrize('config', [{}, {'framework': 'mlx'}, {'recipe': 'unknown', 'framework': 'mlx'}, {'recipe': 'tool-calls', 'framework': 'mlx'}])
def test_missing_explicit_base_cannot_fall_back_even_with_empty_configuration(assets, config):
    model, _, destination = assets
    write_json(model / 'state.json', {'step': 1, 'config': config})
    with pytest.raises(ValueError, match='explicit'):
        prepare_checkpoint(model, None, files(model), destination)
    assert not destination.exists()


def test_missing_config_with_a_valid_base_is_still_not_a_default_recipe(assets):
    model, _, destination = assets
    write_json(model / 'state.json', {'step': 1})
    with pytest.raises(ValueError, match='supported'):
        prepare(assets)
    assert not destination.exists()


@pytest.mark.parametrize('change', ['digest', 'base-bytes', 'sidecar', 'missing-sidecar', 'identity-directory', 'missing-identity', 'identity-empty', 'identity-mismatch'])
def test_unmatched_or_missing_frozen_base_identity_is_refused(assets, change):
    model, base, destination = assets
    state = json.loads((model / 'state.json').read_text())
    if change == 'digest':
        base['digest'] = '0' * 64
    elif change == 'base-bytes':
        (Path(base['path']) / 'model.safetensors').write_bytes(b'substituted base')
    elif change == 'sidecar':
        write_json(model / 'studio-model.json', {'version': 1, 'base_digest': '0' * 64})
    elif change == 'missing-sidecar':
        (model / 'studio-model.json').unlink()
    elif change == 'identity-directory':
        state['config']['mlx_base_identity']['directory'] = '/private/elsewhere'
    elif change == 'missing-identity':
        state['config'].pop('mlx_base_identity')
    elif change == 'identity-empty':
        state['config']['mlx_base_identity'] = {'files': {}}
    else:
        state['config']['mlx_base_identity']['files']['model.safetensors'] = '0' * 64
    write_json(model / 'state.json', state)
    with pytest.raises(ValueError):
        prepare(assets)
    assert not destination.exists()


@pytest.mark.parametrize('target', ['../private.safetensors', '/private/model.safetensors', 'missing.safetensors', 'config.json', 'sub/../../private.safetensors'])
def test_tensor_index_references_only_declared_relative_weight_files(assets, target):
    model, _, destination = assets
    write_json(model / 'model.safetensors.index.json', {'metadata': {'total_size': 10}, 'weight_map': {'layer.weight': target}})
    with pytest.raises(ValueError):
        prepare(assets)
    assert not destination.exists()


def test_declared_sharded_weights_survive_relocation(assets):
    model, _, destination = assets
    (model / 'model.safetensors').unlink()
    (model / 'weights').mkdir()
    (model / 'weights' / 'part.safetensors').write_bytes(b'sharded bytes')
    write_json(model / 'model.safetensors.index.json', {'weight_map': {'model': 'weights/part.safetensors'}})
    result = prepare(assets)
    index = json.loads((Path(result['path']) / 'model.safetensors.index.json').read_text())
    assert index['weight_map'] == {'model': 'weights/part.safetensors'}
    assert (destination / 'weights' / 'part.safetensors').read_bytes() == b'sharded bytes'


@pytest.mark.parametrize('kind', ['symbolic', 'hard', 'undeclared', 'parent-symbolic'])
def test_linked_and_undeclared_checkpoint_assets_are_rejected(assets, tmp_path, kind):
    model, base, destination = assets
    declared = files(model)
    if kind == 'undeclared':
        (model / 'extra.safetensors').write_bytes(b'unlisted')
    elif kind == 'parent-symbolic':
        link = tmp_path / 'linked-checkpoint'
        link.symlink_to(model, target_is_directory=True)
        model = link
    else:
        weight = model / 'model.safetensors'
        saved = tmp_path / 'original-weights'
        weight.rename(saved)
        if kind == 'symbolic':
            weight.symlink_to(saved)
        else:
            os.link(saved, weight)
    with pytest.raises((ValueError, OSError)):
        prepare_checkpoint(model, base, declared, destination)
    assert not destination.exists()


def test_changed_existing_output_conflicts_and_never_rewrites_the_source(assets):
    model, _, destination = assets
    prepare(assets)
    original = (model / 'model.safetensors').read_bytes()
    (destination / 'model.safetensors').write_bytes(b'changed output')
    with pytest.raises(ValueError, match='digest'):
        prepare(assets)
    assert (model / 'model.safetensors').read_bytes() == original
    assert not destination.with_name(destination.name + '.partial').exists()


@pytest.mark.parametrize('metadata', ['{"step":1,"config":{"recipe":"tool-calls","framework":"mlx"},"extra":NaN}', '{"step":1,"step":2,"config":{}}'])
def test_nonfinite_and_duplicate_metadata_fields_are_refused(assets, metadata):
    model, _, destination = assets
    (model / 'state.json').write_text(metadata)
    with pytest.raises(ValueError):
        prepare(assets)
    assert not destination.exists()


@pytest.mark.parametrize('recipe', ['classify-text', 'text-lm'])
def test_fixed_standalone_recipes_have_no_ambient_base_dependency(assets, recipe):
    model, _, destination = assets
    write_json(model / 'state.json', {'step': 1, 'config': {'recipe': recipe, 'framework': 'torch'}})
    (model / 'studio-model.json').unlink()
    result = prepare_checkpoint(model, None, files(model), destination)
    assert result['base'] is result['base_digest'] is None
    assert json.loads((destination / 'state.json').read_text())['config']['recipe'] == recipe


@pytest.mark.parametrize('field,value', [('base', '/private/forbidden'), ('lora', True), ('tokenizer_file', '/private/forbidden'), ('auto_map', {'Model': 'injected'})])
def test_relabelled_standalone_checkpoint_cannot_assert_external_dependency(assets, field, value):
    model, _, destination = assets
    (model / 'studio-model.json').unlink()
    config = {'recipe': 'classify-text', 'framework': 'torch', field: value}
    write_json(model / 'state.json', {'step': 1, 'config': config})
    with pytest.raises(ValueError, match='dependencies'):
        prepare_checkpoint(model, None, files(model), destination)


def test_base_index_and_tokenizer_references_cannot_escape_staged_base(assets):
    model, base, destination = assets
    base_root = Path(base['path'])
    write_json(base_root / 'tokenizer_config.json', {'tokenizer_file': '../outside.json'})
    base['files'] = files(base_root)
    base['digest'] = identity(base['files'])
    write_json(model / 'studio-model.json', {'version': 1, 'base_digest': base['digest']})
    with pytest.raises(ValueError, match='directory'):
        prepare(assets)
    assert not destination.exists()


def test_standalone_recipe_relabel_cannot_retain_external_sidecar(assets):
    model, _, destination = assets
    write_json(model / 'state.json', {'step': 1, 'config': {'recipe': 'classify-text', 'framework': 'torch'}})
    with pytest.raises(ValueError, match='dependencies'):
        prepare_checkpoint(model, None, files(model), destination)


def test_nested_unregistered_standalone_configuration_is_not_a_builder_contract(assets):
    model, _, destination = assets
    (model / 'studio-model.json').unlink()
    write_json(model / 'state.json', {'step': 1, 'config': {'recipe': 'text-lm', 'framework': 'torch', 'runtime': {'base': '/private'}}})
    with pytest.raises(ValueError, match='schema'):
        prepare_checkpoint(model, None, files(model), destination)

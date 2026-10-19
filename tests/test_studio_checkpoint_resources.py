"""Checkpoint paths cannot nominate the frozen model used for reuse."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.studio import resources, worker
from poolhouse.studio.adapters import Adapters


def test_materialized_checkpoint_omits_recovery_links_and_logs(tmp_path):
    training = tmp_path / 'training'
    checkpoint = training / 'step20'
    checkpoint.mkdir(parents=True)
    (checkpoint / 'state.json').write_text('{"config":{}}')
    (checkpoint / 'model.safetensors').write_bytes(b'weights')
    (training / 'latest').symlink_to(checkpoint.name, target_is_directory=True)
    (training / 'logs.jsonl').write_text('private history')
    (training / 'adapter').mkdir()
    (training / 'adapter/adapters.safetensors').write_bytes(b'adapter')
    output = tmp_path / 'output'
    first = resources.materialize_checkpoint(checkpoint, training, output)
    assert resources.materialize_checkpoint(checkpoint, training, output) == first
    assert {p.relative_to(first).as_posix() for p in first.rglob('*') if p.is_file()} == {
        'state.json', 'model.safetensors', 'adapter/adapters.safetensors'}
    assert not any(p.is_symlink() for p in first.rglob('*'))


def test_materialization_refuses_adapter_directory_link(tmp_path):
    training, private = tmp_path / 'training', tmp_path / 'private'
    checkpoint = training / 'step20'
    checkpoint.mkdir(parents=True)
    (checkpoint / 'state.json').write_text('{}')
    (checkpoint / 'model.safetensors').write_bytes(b'weights')
    private.mkdir()
    (private / 'adapters.safetensors').write_bytes(b'private')
    (training / 'adapter').symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError, match='regular owned'):
        resources.materialize_checkpoint(checkpoint, training, tmp_path / 'output')


def test_checkpoint_missing_lineage_base_fails_before_gpu_hold(tmp_path, monkeypatch):
    (tmp_path / 'state.json').write_text('{"config":{"recipe":"tool-calls","framework":"mlx"}}')
    monkeypatch.setattr(worker.gpu, 'hold', lambda purpose: pytest.fail('invalid lineage must fail before compute'))
    with pytest.raises(ValueError, match='authorized producer base'):
        worker.execute({'type': 'generate'}, {'model': {'path': str(tmp_path), 'recipe': 'tool-calls'},
                                             'prompt': 'question'}, tmp_path)


def test_explicit_staged_base_digest_is_verified_before_recipe_load(tmp_path, monkeypatch):
    base = tmp_path / 'base'
    base.mkdir()
    (base / 'config.json').write_text('{"model_type":"sample"}')
    handle = {'kind': 'model', 'path': str(base)}
    digest = resources.base_fingerprint(handle)
    checkpoint = tmp_path / 'checkpoint'
    checkpoint.mkdir()
    (checkpoint / 'state.json').write_text('{"config":{"recipe":"tool-calls","framework":"mlx"}}')
    model = {'recipe': 'tool-calls', 'path': str(checkpoint), 'base': handle, 'base_digest': digest}
    calls = []
    monkeypatch.setattr(resources.optional, 'module', lambda name: SimpleNamespace(
        validate_base=lambda path, override, **kwargs: calls.append((path, override)) or Path(override)))
    assert resources.checkpoint_base(model) == base
    assert calls == [(model['path'], str(base))]
    (base / 'config.json').write_text('{"model_type":"changed"}')
    with pytest.raises(ValueError, match='producer base content changed'):
        resources.checkpoint_base(model)
    assert len(calls) == 1


def test_fixed_server_model_resolver_is_not_selected_by_workflow_data(tmp_path, monkeypatch):
    from poolhouse.studio import adapters

    monkeypatch.setattr(adapters.hub, 'discover', lambda: [])
    binding = {'kind': 'model', 'id': 'hf:missing/model'}
    context = {'bindings': {'model': binding}, 'staged_models': {'forged': binding}}
    with pytest.raises(ValueError, match='local model library'):
        Adapters(None).immediate({'type': 'model'}, {}, context)
    staged = {'kind': 'model', 'path': str(tmp_path), 'format': 'mlx', 'model_kind': 'chat'}
    accepted = json.loads(json.dumps(binding))

    def server_resolver(request, server_context):
        if request != server_context['staged_models'].get('accepted'):
            raise ValueError('Unbound staged model')
        return staged

    fixed = Adapters(None, model_resolver=server_resolver)
    context['staged_models'] = {'accepted': accepted}
    assert fixed.immediate({'type': 'model'}, {}, context) == {'model': staged}
    context['bindings']['model'] = {**binding, 'path': '/private/model'}
    with pytest.raises(ValueError, match='Unbound staged model'):
        fixed.immediate({'type': 'model'}, {}, context)


def test_materialization_refuses_preexisting_output_adapter_link(tmp_path):
    training, private = tmp_path / 'training', tmp_path / 'private'
    checkpoint = training / 'step20'
    checkpoint.mkdir(parents=True)
    (checkpoint / 'state.json').write_text('{}')
    (checkpoint / 'model.safetensors').write_bytes(b'weights')
    (training / 'adapter').mkdir()
    (training / 'adapter/adapters.safetensors').write_bytes(b'adapter')
    output = tmp_path / 'output'
    (output / checkpoint.name).mkdir(parents=True)
    private.mkdir()
    (output / checkpoint.name / 'adapter').symlink_to(private, target_is_directory=True)
    with pytest.raises(ValueError, match='output directory cannot contain links'):
        resources.materialize_checkpoint(checkpoint, training, output)
    assert list(private.iterdir()) == []


@pytest.mark.parametrize('claimed', ['text-lm', 'classify-text', ''])
@pytest.mark.parametrize('kind', ['evaluate', 'generate'])
def test_recorded_conversation_checkpoint_cannot_hide_base_requirement(tmp_path, monkeypatch, claimed, kind):
    (tmp_path / 'state.json').write_text('{"config":{"recipe":"tool-calls","framework":"mlx","base":"/private/model"}}')
    monkeypatch.setattr(worker.gpu, 'hold', lambda purpose: pytest.fail('forged recipe must fail before compute'))
    model = {'path': str(tmp_path), 'recipe': claimed}
    with pytest.raises(ValueError, match='recipe metadata'):
        worker.execute({'type': kind, 'parameters': {'suite': 'tool-calls'}},
                       {'model': model, 'dataset': {'path': str(tmp_path)}, 'prompt': 'question'}, tmp_path)

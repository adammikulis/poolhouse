"""MLX serving readiness follows the app's declared engine extra."""
from types import SimpleNamespace

import pytest

from poolhouse.fleet.environment import CATALOG
from poolhouse.serve import python_requirements as dependencies
from poolhouse.studio.adapters import Adapters


@pytest.fixture
def contract(monkeypatch):
    monkeypatch.setattr(dependencies, 'declared', lambda: {'spec': [
        'mlx>=0.32.2', 'mlx-lm>=0.31.3', 'mlx-vlm>=0.7.1',
        'excluded>=99; python_version < "2"']})
    return {'mlx': '0.32.3', 'mlx-lm': '0.32.2', 'mlx-vlm': '0.7.1'}


def test_library_installs_the_declared_serving_extra():
    library = next(item for item in CATALOG if item.name == 'serve-mlx')
    assert library.packages == ('poolhouse[spec]',)
    assert library.platforms == ('darwin',) and library.vendors == ('apple',)


@pytest.mark.parametrize('found', [None, '0.6.9', 'invalid'])
def test_missing_and_old_multimodal_dependency_are_unavailable(contract, found):
    if found is None:
        del contract['mlx-vlm']
    else:
        contract['mlx-vlm'] = found
    assert dependencies.missing_mlx(contract) == ['mlx-vlm>=0.7.1']


def test_declared_versions_and_markers_control_readiness(contract, monkeypatch):
    assert dependencies.missing_mlx(contract) == []
    monkeypatch.setattr(dependencies, 'version', lambda name: contract[name])
    assert dependencies.installed_mlx()
    monkeypatch.setattr(dependencies, 'declared', lambda: {})
    assert not dependencies.installed_mlx()
    with pytest.raises(OSError, match='contract'):
        dependencies.missing_mlx(contract)


def test_model_bound_preflight_and_target_prepare_refuse_missing_spec_dependency(contract, tmp_path):
    del contract['mlx-vlm']
    env = SimpleNamespace(exists=True, installed=lambda: contract, require_current_runtime=lambda: None)
    model = {'kind': 'model', 'format': 'mlx', 'path': str(tmp_path)}
    adapters = Adapters(SimpleNamespace(environment=env), model_resolver=lambda binding, context: model)
    graph = {'nodes': [{'id': 'm', 'type': 'model'}, {'id': 'g', 'type': 'generate'}],
             'edges': [{'source': {'node': 'm', 'port': 'model'}, 'target': {'node': 'g', 'port': 'model'}}]}
    errors = adapters.validate_resources(graph)
    assert errors[0]['node'] == 'g' and 'MLX model serving' in errors[0]['message']
    with pytest.raises(ValueError, match='mlx-vlm'):
        adapters.prepare(graph['nodes'][1], {'model': model}, {'output_dir': tmp_path})
    assert not (tmp_path / 'request.json').exists()
    contract['mlx-vlm'] = '0.7.1'
    assert adapters.validate_resources(graph) == []


def test_native_trained_generation_and_holdout_do_not_require_spec(contract, tmp_path):
    del contract['mlx-vlm']
    env = SimpleNamespace(exists=True, installed=lambda: contract)
    model = {'kind': 'model', 'format': 'mlx', 'recipe': 'tool-calls', 'path': str(tmp_path)}
    adapters = Adapters(SimpleNamespace(environment=env), model_resolver=lambda binding, context: model)
    graph = {'nodes': [{'id': 'm', 'type': 'model'}, {'id': 'g', 'type': 'generate'}],
             'edges': [{'source': {'node': 'm', 'port': 'model'}, 'target': {'node': 'g', 'port': 'model'}}]}
    assert adapters.validate_resources(graph) == []
    model.pop('recipe')
    graph['nodes'][1] = {'id': 'g', 'type': 'evaluate', 'parameters': {'suite': 'holdout'}}
    assert adapters.validate_resources(graph) == []


def test_device_serving_capability_requires_full_contract_without_changing_array_backend(monkeypatch):
    from poolhouse.fleet import accelerator

    monkeypatch.setattr(accelerator, 'find_spec', lambda name: object() if name in {'mlx', 'mlx_lm', 'mlx_vlm'} else None)
    monkeypatch.setattr(accelerator, 'detect_device', lambda: SimpleNamespace(
        vendor='apple', name='Test device', total_memory_gb=16, unified_memory=True))
    for name in ('torch_report', 'mlx_report', 'gpu_telemetry', 'apple_telemetry'):
        monkeypatch.setattr(accelerator, name, lambda: {})
    monkeypatch.setattr(accelerator, 'installed_mlx', lambda: False)
    assert accelerator.report()['backends'] == ['mlx']
    assert accelerator.report()['serving_backends'] == []
    monkeypatch.setattr(accelerator, 'installed_mlx', lambda: True)
    assert accelerator.report()['serving_backends'] == ['mlx-tree']

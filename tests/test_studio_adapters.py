"""Structured Studio worker execution and managed adapter boundaries."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.studio.adapters import Adapters, confined
from poolhouse.studio.worker import execute, run


def test_split_validation_and_lineage(tmp_path):
    data = tmp_path / 'input.jsonl'
    data.write_text(''.join(json.dumps({'text': str(i), 'label': str(i % 2)}) + '\n' for i in range(10)))
    resource = {'kind': 'dataset', 'path': str(data)}
    validated = execute({'type': 'data.validate'}, {'dataset': resource}, tmp_path)
    assert validated['report'] == {'valid': True, 'examples': 10}
    split = execute({'type': 'data.split', 'parameters': {'fraction': .7, 'seed': 3}},
                    {'dataset': resource}, tmp_path)
    assert split['train']['rows'] == 7
    assert split['test']['rows'] == 3
    train = Path(split['train']['path']) / 'data.jsonl'
    test = Path(split['test']['path']) / 'data.jsonl'
    assert set(train.read_text().splitlines()).isdisjoint(test.read_text().splitlines())


def test_resources_refuse_traversal_and_symlinks(tmp_path):
    root = tmp_path / 'files'
    root.mkdir()
    private = tmp_path / 'private'
    private.write_text('secret')
    (root / 'linked').symlink_to(private)
    for path in ('../private', 'linked', str(private)):
        with pytest.raises(ValueError, match='selected project'):
            confined(root, path)


def test_managed_worker_fixed_command_and_structured_collection(tmp_path):
    environment = SimpleNamespace(exists=True, python=Path('/managed/python'), require_current_runtime=lambda: None)
    adapter = Adapters(SimpleNamespace(environment=environment))
    context = {'output_dir': tmp_path, 'bindings': {}, 'files_root': tmp_path}
    node = {'id': 'a', 'type': 'report', 'parameters': {}}
    prepared = adapter.prepare(node, {'report': {'loss': 1.2}}, context)
    assert prepared['argv'] == ['/managed/python', '-B', '-I', '-m', 'poolhouse.studio.worker', str(tmp_path / 'request.json')]
    with pytest.raises(ValueError, match='structured result'):
        adapter.collect(node, {}, context)
    (tmp_path / 'result.json').write_text(json.dumps({'outputs': {'artifact': {'path': 'report.json'}}}))
    assert adapter.collect(node, {}, context)['artifact']['path'] == 'report.json'


def test_training_smoke_does_not_declare_model_or_checkpoint(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from poolhouse import optional
    from poolhouse.serve import gpu
    held = []

    @contextmanager
    def hold(purpose):
        held.append(purpose)
        yield

    def run(recipe, config, data, out, dry):
        assert held and dry
        return {'dry_run': True, 'checkpoint': '', 'final_loss': 0.2}

    recipes = SimpleNamespace(validate=lambda recipe, config: config)
    monkeypatch.setattr(gpu, 'hold', hold)
    monkeypatch.setattr(optional, 'module', lambda name: recipes if name.endswith('recipes') else SimpleNamespace(run=run))
    outputs = execute({'type': 'train.classify-text', 'parameters': {'mode': 'smoke'}},
                      {'dataset': {'path': str(tmp_path)}}, tmp_path)
    assert set(outputs) == {'metrics'}
    assert outputs['metrics']['dry_run']


def test_worker_writes_result_without_scraping_stdout(tmp_path, monkeypatch):
    import sys
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'node': {'type': 'report'}, 'inputs': {'report': {'loss': .2}},
                                   'output_dir': str(tmp_path)}))
    monkeypatch.setattr(sys, 'argv', ['worker', str(request)])
    assert run() == 0
    result = json.loads((tmp_path / 'result.json').read_text())
    assert json.loads(Path(result['outputs']['artifact']['path']).read_text()) == {'loss': .2}


def test_imported_operation_never_selects_executable(tmp_path):
    with pytest.raises(ValueError, match='execution integration'):
        execute({'type': 'os.system', 'parameters': {'argv': ['bad']}}, {}, tmp_path)


def test_training_checkpoint_evaluation_and_export(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from poolhouse.serve import gpu

    monkeypatch.setattr(gpu, 'hold', lambda purpose: nullcontext())
    monkeypatch.setenv('POOLHOUSE_DEVICE', 'cpu')
    source = tmp_path / 'labelled.jsonl'
    source.write_text(''.join(json.dumps({'text': f'example {i}', 'label': 'even' if i % 2 else 'odd'}) + '\n'
                              for i in range(40)))
    result = execute({'type': 'train.classify-text', 'parameters': {'framework': 'torch',
                     'size': 'small', 'steps': 20, 'context': 32, 'batch_size': 2}},
                     {'dataset': {'path': str(source)}}, tmp_path)
    assert Path(result['checkpoint']['path']).is_dir()
    evaluated = execute({'type': 'evaluate'}, {'model': result['model'],
                        'dataset': {'path': str(source)}}, tmp_path)
    assert evaluated['metrics']['loss'] >= 0
    subset = tmp_path / 'subset.jsonl'
    subset.write_text(''.join(json.dumps({'text': f'single class {i}', 'label': 'even'}) + '\n' for i in range(6)))
    subset_score = execute({'type': 'evaluate'}, {'model': result['model'], 'dataset': {'path': str(subset)}}, tmp_path)
    assert 0 <= subset_score['metrics']['accuracy'] <= 1
    exported = execute({'type': 'export', 'parameters': {'format': 'checkpoint'}},
                       {'model': result['model']}, tmp_path)
    files = {path.name for path in Path(exported['artifact']['path']).iterdir()}
    assert files == {'model.safetensors', 'state.json', 'export.json'}


def test_export_rejects_linked_weights(tmp_path):
    source = tmp_path / 'training'
    adapter = source / 'adapter'
    adapter.mkdir(parents=True)
    secret = tmp_path / 'secret'
    secret.write_text('private')
    (adapter / 'adapter_model.safetensors').symlink_to(secret)
    with pytest.raises(ValueError, match='regular owned'):
        execute({'type': 'export', 'parameters': {'format': 'adapter'}},
                {'model': {'path': str(source), 'recipe': 'tool-calls'}}, tmp_path)


def test_generation_uses_broker_endpoint_and_releases(tmp_path, monkeypatch):
    from poolhouse.studio import worker

    released = []
    calls = []
    monkeypatch.setattr(worker.broker_wire, 'lease', lambda *args, **kwargs:
                        SimpleNamespace(base_url='http://127.0.0.1:1234', model='selected', lease='owned'))
    monkeypatch.setattr(worker.broker_wire, 'release', released.append)

    class Client:
        def __init__(self, url, model):
            assert url == 'http://127.0.0.1:1234'
            assert model == 'selected'

        def chat(self, messages, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content='{"answer": 4}', tool_calls=None)

    monkeypatch.setattr(worker, 'Client', Client)
    result = execute({'type': 'generate.structured', 'parameters': {'json_schema': '{"type":"object"}'}},
                     {'model': {'path': 'selected'}, 'prompt': 'question'}, tmp_path)
    assert result['response'] == {'answer': 4}
    assert calls[0]['response_format']['type'] == 'json_schema'
    assert released == ['owned']


def test_authorized_tools_use_explicit_callback(tmp_path):
    calls = []
    adapter = Adapters(None, tool_call=lambda name, args, context: calls.append((name, args, context['principal'])) or {'ok': True})
    node = {'type': 'tools.call', 'parameters': {'tool': 'declared'}}
    assert adapter.immediate(node, {}, {}) is None
    assert adapter.supports_authorized(node)
    assert adapter.execute_authorized(node, {'arguments': {'x': 2}}, {'principal': {'session': 'current'}}) == {'result': {'ok': True}}
    assert calls == [('declared', {'x': 2}, {'session': 'current'})]
    with pytest.raises(ValueError, match='authorized tool integration'):
        Adapters(None, tool_call=None).execute_authorized(node, {'arguments': {}}, {})


def test_model_binding_resolves_installed_snapshot_without_fetch(tmp_path, monkeypatch):
    from poolhouse.studio import adapters

    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    monkeypatch.setattr(adapters.hub, 'held_snapshot', lambda repo: snapshot if repo == 'sample/model' else None)
    context = {'bindings': {'model': {'kind': 'model', 'id': 'sample/model'}}, 'files_root': tmp_path}
    result = Adapters(None).immediate({'type': 'model'}, {}, context)
    assert result['model']['path'] == str(snapshot)
    context['bindings']['model']['id'] = '../private'
    with pytest.raises(ValueError, match='local model library'):
        Adapters(None).immediate({'type': 'model'}, {}, context)


def test_default_tools_are_fixed_read_operations_and_checked(monkeypatch):
    from poolhouse.studio import adapters

    calls = []
    checked = []
    schema = {'function': {'name': 'serve_status'}}
    def function(**args):
        calls.append(args)
        return {'ready': True}

    modules = {'poolhouse.chatpolicy': SimpleNamespace(READ={'serve_status', 'serve_up'}),
               'poolhouse.do': SimpleNamespace(command_tools=lambda: [(schema, function)]),
               'poolhouse.mcp': SimpleNamespace(checked=lambda fn, args: checked.append((fn, args)))}
    monkeypatch.setattr(adapters.optional, 'module', modules.__getitem__)
    assert adapters.tool_catalog() == [schema]
    assert adapters.governed_tool('serve_status', {}, {'principal': {'session': 'current'}}) == {'ready': True}
    assert checked == [(function, {})]
    assert calls == [{}]
    with pytest.raises(ValueError, match='not available'):
        adapters.governed_tool('serve_up', {}, {'principal': {}})
    with pytest.raises(ValueError, match='authenticated'):
        adapters.governed_tool('serve_status', {}, {})


def test_templates_cover_lifecycle_and_keep_resources_unbound():
    from poolhouse.studio.templates import templates

    library = templates()
    assert len(library) == 6
    assert all(graph['schema_version'] == 1 and isinstance(graph['groups'], dict) for graph in library)
    for graph in library:
        assert all(not node['bindings'] for node in graph['nodes'])
        assert all(edge['source']['node'] in {node['id'] for node in graph['nodes']} for edge in graph['edges'])
    tools = next(graph for graph in library if graph['id'] == 'inference-and-tools')
    assert next(node for node in tools['nodes'] if node['type'] == 'tools.call')['parameters']['tool'] == 'serve_status'


def test_dependencies_checked_in_managed_runtime_before_dispatch(monkeypatch):
    probes = []
    environment = SimpleNamespace(exists=True, require_current_runtime=lambda: None,
                                  installed=lambda: probes.append('managed') or {'torch': '2'})
    adapter = Adapters(SimpleNamespace(environment=environment))
    monkeypatch.setattr(adapter, 'dependency_fingerprint', lambda node: probes.append('code') or 'digest')
    errors = adapter.validate([{'id': 'data', 'type': 'dataset'},
                               {'id': 'train', 'type': 'train.tool-calls', 'parameters': {'framework': 'mlx'}},
                               {'id': 'gym', 'type': 'gym.train'}])
    assert probes == ['managed', 'code']
    assert {error['node'] for error in errors} == {'train', 'gym'}
    assert 'mlx-lm' in errors[0]['message']
    assert Adapters(None).validate([{'id': 'prompt', 'type': 'prompt'}]) == []


def test_missing_runtime_fails_before_worker_submission():
    errors = Adapters(SimpleNamespace(environment=None)).validate([{'id': 'split', 'type': 'data.split'}])
    assert errors == [{'node': 'split', 'code': 'dependency', 'message': 'Install the managed compute runtime to run this node'}]


def test_fine_tuning_requires_authorized_model_binding(tmp_path):
    with pytest.raises(ValueError, match='authorized base model'):
        execute({'type': 'train.tool-calls', 'parameters': {'base': '/unbound/private'}},
                {'dataset': {'path': str(tmp_path)}}, tmp_path)


def test_base_and_trained_holdout_share_native_loss_contract(tmp_path, monkeypatch):
    from contextlib import nullcontext

    from poolhouse.studio import worker

    calls = []
    built = SimpleNamespace(config={'batch_size': 2, 'holdout_rows': 4, 'recipe': 'tool-calls'},
                            eval_batches=lambda index: index, batches=None,
                            step=SimpleNamespace(eval_loss=lambda batch: .5), predict=None)

    def build(recipe, config, data):
        calls.append(('build', recipe, config, data))
        return built

    def load(path, data):
        calls.append(('load', path, data))
        return built

    monkeypatch.setattr(worker.gpu, 'hold', lambda purpose: nullcontext())
    monkeypatch.setattr(worker.optional, 'module', lambda name: SimpleNamespace(build=build, load=load))
    dataset = {'path': str(tmp_path / 'dataset')}
    base = execute({'type': 'evaluate', 'parameters': {'suite': 'holdout', 'framework': 'mlx'}},
                   {'model': {'path': str(tmp_path)}, 'dataset': dataset}, tmp_path)
    trained = execute({'type': 'evaluate'}, {'model': {'path': str(tmp_path), 'recipe': 'tool-calls'},
                      'dataset': dataset}, tmp_path)
    assert base['metrics']['loss'] == trained['metrics']['loss'] == .5
    assert calls[0][0] == 'build' and calls[0][2]['lora']
    assert calls[1][0] == 'load'
    compared = execute({'type': 'compare'}, {'base': base['metrics'], 'trained': trained['metrics']}, tmp_path)
    assert compared['metrics']['loss'] == 0


def test_trained_mlx_generation_holds_compute_and_quantizes_cache(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from poolhouse.studio import worker

    held = []
    generated = []

    @contextmanager
    def hold(purpose):
        held.append(True)
        yield
        held.pop()

    tokenizer = SimpleNamespace(apply_chat_template=lambda messages, **kwargs: 'rendered')
    built = SimpleNamespace(model='weights', config={'framework': 'mlx', 'recipe': 'tool-calls', 'base': 'owned-base'})

    def generate(model, tokenizer, **kwargs):
        assert held and model == 'weights'
        generated.append(kwargs)
        return 'answer'

    modules = {'poolhouse.train.recipes': SimpleNamespace(load=lambda path: built),
               'transformers': SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=lambda *args, **kwargs: tokenizer)),
               'mlx_lm.sample_utils': SimpleNamespace(make_sampler=lambda **kwargs: 'sampler'),
               'mlx_lm': SimpleNamespace(generate=generate)}
    monkeypatch.setattr(worker.gpu, 'hold', hold)
    monkeypatch.setattr(worker.optional, 'module', modules.__getitem__)
    result = execute({'type': 'generate', 'parameters': {'max_tokens': 8}},
                     {'model': {'path': str(tmp_path), 'recipe': 'tool-calls'}, 'prompt': 'question'}, tmp_path)
    assert result['text'] == 'answer'
    assert generated[0]['kv_bits'] == 8
    assert generated[0]['quantized_kv_start'] == 0
    assert generated[0]['max_tokens'] == 8
    assert held == []


@pytest.mark.parametrize('schema', ['{"$ref":"https://invalid.example/schema"}', '{"type":"program"}', '{"anyOf":[]}'])
def test_response_schemas_refuse_external_or_unsupported_rules(schema):
    from poolhouse.studio.adapters import response_schema

    with pytest.raises(ValueError):
        response_schema(schema)

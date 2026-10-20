"""Saved model references resolve only live authorized producer artifacts."""
import copy
import hashlib

import pytest

from poolhouse.studio.engine import _artifacts
from poolhouse.studio.saved_models import SavedModels
from poolhouse.studio.store import digest


class MemoryStore:
    def __init__(self, row):
        self.rows = {row['id']: row}

    def records(self, kind, scope):
        return [copy.deepcopy(row) for row in self.rows.values() if row['scope'] == scope]

    def get(self, kind, ident, scope):
        row = self.rows[ident]
        if row['scope'] != scope:
            raise KeyError(ident)
        return copy.deepcopy(row)


@pytest.fixture
def saved(tmp_path):
    principal = {'id': 'caller', 'scope': 'pool', 'owner': False}
    node = {'id': 'trainer', 'type': 'train.text-lm', 'version': 1, 'parameters': {}}
    root = tmp_path / 'studio' / ('a' * 32) / hashlib.sha256(b'trainer').hexdigest()[:24] / 'checkpoint'
    root.mkdir(parents=True)
    (root / 'state.json').write_text('{"recipe":"text-lm","framework":"torch"}')
    (root / 'model.safetensors').write_bytes(b'weights')
    outputs = {'model': {'kind': 'model', 'path': str(root), 'recipe': 'text-lm', 'framework': 'torch'},
               'checkpoint': {'kind': 'artifact', 'path': str(root)}, 'metrics': {}}
    state = {'state': 'done', 'outputs': outputs, 'fingerprint': 'fixed',
             'output_digest': digest(outputs), 'artifacts': _artifacts(node, outputs, 'outputs')}
    row = {'id': 'a' * 32, 'scope': 'pool', 'principal': principal, 'title': 'Completed training',
           'graph': {'nodes': [node], 'edges': []}, 'nodes': {'trainer': state}}
    store = MemoryStore(row)
    allowed = {'value': True}
    registry = SavedModels(store, tmp_path, lambda principal, node, inputs: allowed['value'])
    return registry, store, principal, node, root, allowed


def test_completed_checkpoint_is_selectable_in_a_separate_workflow(saved):
    registry, store, principal, _node, root, _ = saved
    rows = registry.list(principal)
    assert len(rows) == 1
    assert str(root) not in str(rows)
    handle = registry.resolve(principal, rows[0]['id'])
    assert handle['path'] == str(root) and 'id' not in handle
    assert handle['studio_reference'] == rows[0]['id']
    registry.check_inputs(principal, {'model': handle})
    handle['recipe'] = 'tool-calls'
    with pytest.raises(ValueError, match='handle changed'):
        registry.check_inputs(principal, {'model': handle})
    assert store.rows['a' * 32]['nodes']['trainer']['outputs']['model']['recipe'] == 'text-lm'


@pytest.mark.parametrize('change', ['scope', 'caller', 'revoked', 'smoke', 'failed', 'deleted', 'bytes', 'metadata', 'escape', 'link'])
def test_unavailable_or_changed_producers_never_resolve(saved, change):
    registry, store, principal, _node, root, allowed = saved
    ident = registry.list(principal)[0]['id']
    row = store.rows['a' * 32]
    state = row['nodes']['trainer']
    if change == 'scope':
        principal = {**principal, 'scope': 'other'}
    elif change == 'caller':
        principal = {**principal, 'id': 'other'}
    elif change == 'revoked':
        allowed['value'] = False
    elif change == 'smoke':
        row['graph']['nodes'][0]['parameters']['mode'] = 'smoke'
    elif change == 'failed':
        state['state'] = 'failed'
    elif change == 'deleted':
        store.rows.clear()
    elif change == 'bytes':
        (root / 'model.safetensors').write_bytes(b'changed')
    elif change == 'metadata':
        state['outputs']['model']['path'] = '/private/unrelated'
    elif change == 'escape':
        state['outputs']['model']['path'] = str(root / '..' / '..')
        state['output_digest'] = digest(state['outputs'])
    elif change == 'link':
        (root / 'linked').symlink_to(root / 'state.json')
    with pytest.raises((ValueError, PermissionError, OSError)):
        registry.resolve(principal, ident)
    assert registry.list(principal) == []


def test_revocation_is_rechecked_after_selection_at_consumer_dispatch(saved):
    registry, _, principal, _, _, allowed = saved
    handle = registry.resolve(principal, registry.list(principal)[0]['id'])
    allowed['value'] = False
    with pytest.raises(PermissionError):
        registry.check_inputs(principal, {'model': handle})


@pytest.mark.parametrize('ident', ['/private/model', 'hf:pretend', 'studio-output:../private', 'studio-output:' + '0' * 64])
def test_no_path_or_installed_model_fallback(saved, ident):
    registry, _, principal, *_ = saved
    with pytest.raises(ValueError):
        registry.resolve(principal, ident)


def test_cached_checkpoint_requires_exact_authorized_original_lineage(saved):
    registry, store, principal, _, _, _allowed = saved
    row = store.rows['a' * 32]
    duplicate = copy.deepcopy(row)
    duplicate['id'] = 'b' * 32
    duplicate['nodes']['trainer']['reused_from'] = row['id']
    store.rows[duplicate['id']] = duplicate
    ident = registry.reference(duplicate, duplicate['graph']['nodes'][0], 'model')
    assert registry.resolve(principal, ident)['path'] == row['nodes']['trainer']['outputs']['model']['path']
    row['nodes']['trainer']['reused_from'] = duplicate['id']
    with pytest.raises(ValueError, match='cycle'):
        registry.resolve(principal, ident)


def test_producer_base_lineage_and_content_are_rechecked(saved, tmp_path):
    registry, store, principal, _, _, _ = saved
    row = store.rows['a' * 32]
    node = row['graph']['nodes'][0]
    node['type'] = 'train.tool-calls'
    base_path = tmp_path / 'base'
    base_path.mkdir()
    (base_path / 'config.json').write_text('{}')
    (base_path / 'model.safetensors').write_bytes(b'base weights')
    from poolhouse.studio.resources import base_fingerprint
    base = {'kind': 'model', 'path': str(base_path)}
    outputs = row['nodes']['trainer']['outputs']
    outputs['model'].update(recipe='tool-calls', base=base, base_digest=base_fingerprint(base))
    source = {'id': 'base', 'type': 'model', 'version': 1}
    row['graph']['nodes'].append(source)
    row['graph']['edges'].append({'source': {'node': 'base', 'port': 'model'}, 'target': {'node': 'trainer', 'port': 'model'}})
    row['nodes']['base'] = {'state': 'done', 'outputs': {'model': base}}
    row['nodes']['trainer'].update(output_digest=digest(outputs), artifacts=_artifacts(node, outputs, 'outputs'))
    ident = registry.reference(row, node, 'model')
    handle = registry.resolve(principal, ident)
    (base_path / 'model.safetensors').write_bytes(b'mutated base')
    with pytest.raises(ValueError, match='base content changed'):
        registry.check_inputs(principal, {'model': handle})


def test_picker_route_includes_authorized_saved_checkpoints(saved, monkeypatch):
    from types import SimpleNamespace

    from poolhouse.fleet import studio_routes
    registry, _, principal, _, root, _ = saved
    replies = []
    engine = SimpleNamespace(store=registry.store, authorize=lambda *args: True)
    handler = SimpleNamespace(path='/ui/studio/resources', method='GET',
                              _service=lambda: (engine, principal),
                              ui=SimpleNamespace(runner=SimpleNamespace(files_root=root.parent), studio_saved_models=registry),
                              send=lambda status, body: replies.append((status, body)))
    monkeypatch.setattr(studio_routes.hub, 'discover', lambda: [SimpleNamespace(id='installed-base')])
    assert studio_routes.StudioRoutes._studio(handler)
    assert replies[0][0] == 200
    models = replies[0][1]['models']
    assert models[0]['id'] == 'installed-base'
    assert models[1]['source'] == 'studio' and models[1]['id'].startswith('studio-output:')


def test_adapter_uses_current_principal_resolver_and_checks_checkpoint_subtype(saved):
    from poolhouse.studio.adapters import Adapters
    registry, _, principal, _, _, _ = saved
    ident = registry.list(principal)[0]['id']
    calls = []
    def resolve(binding, context):
        calls.append(context['principal'])
        return registry.resolve(context['principal'], binding['id'])
    adapter = Adapters(None, model_resolver=resolve)
    graph = {'nodes': [{'id': 'saved', 'type': 'model', 'bindings': {'model': {'kind': 'model', 'id': ident}}},
                       {'id': 'eval', 'type': 'evaluate.decision'}],
             'edges': [{'source': {'node': 'saved', 'port': 'model'}, 'target': {'node': 'eval', 'port': 'model'}}]}
    assert adapter.validate_resources(graph, {'principal': principal})[0]['node'] == 'eval'
    graph['nodes'][1]['type'] = 'evaluate'
    assert adapter.validate_resources(graph, {'principal': principal}) == []
    assert calls == [principal, principal]

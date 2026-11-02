"""Stable resident source paths retain current authority without native inference."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from poolhouse.decide.sources import Source
from poolhouse.fleet.studio_decision_service import StableDecisionModels


@pytest.fixture
def models(tmp_path, monkeypatch):
    state = {'authorized': True, 'identity': 'original', 'materialized': []}
    original = tmp_path / 'original'
    original.mkdir()
    (original / 'model.safetensors').write_bytes(b'base')
    engine = SimpleNamespace(runner=SimpleNamespace(files_root=tmp_path / 'files'), implementation='a' * 64,
                             authorize=lambda *args: state['authorized'],
                             adapters=SimpleNamespace(dependency_fingerprint=lambda node: 'dependency'))
    def source(model, directory):
        if directory is None:
            return None, {'model': state['identity']}
        directory.mkdir(parents=True, exist_ok=True)
        head = directory / 'head.safetensors'
        head.write_bytes(b'head')
        state['materialized'].append(str(directory))
        return Source('verified', original, original, head, None, 2, 1.0, details=None), {'model': state['identity']}
    monkeypatch.setattr('poolhouse.fleet.studio_decision_service.source', source)
    monkeypatch.setattr('poolhouse.fleet.studio_decision_service.peak_estimate', lambda value: 1024)
    resolver = StableDecisionModels(engine)
    inputs = {'model': {'kind': 'model', 'path': str(original)}}
    context = {'principal': {'id': 'current'}, 'output_dir': tmp_path / 'event-one'}
    return resolver, inputs, context, state


def test_warm_records_retain_exact_source_without_materializing_per_event(models):
    resolver, inputs, context, state = models
    first = resolver({}, inputs, {**context, 'decision_materialize': True})
    second = resolver({}, inputs, {**context, 'output_dir': Path('different-event')})
    assert first == second and len(state['materialized']) == 1
    assert first.source['details'] is None
    assert 'event-one' not in first.source['head']
    assert resolver({}, inputs, {**context, 'decision_materialize': True}) == first
    assert len(state['materialized']) == 1


def test_current_callback_never_materializes_new_or_changed_content(models):
    resolver, inputs, context, state = models
    with pytest.raises(PermissionError, match='no current owned'):
        resolver({}, inputs, context)
    resolver({}, inputs, {**context, 'decision_materialize': True})
    state['identity'] = 'changed'
    with pytest.raises(PermissionError, match='no current owned'):
        resolver({}, inputs, context)
    assert len(state['materialized']) == 1


def test_current_revocation_and_owned_substitution_refuse(models):
    resolver, inputs, context, state = models
    first = resolver({}, inputs, {**context, 'decision_materialize': True})
    state['authorized'] = False
    with pytest.raises(PermissionError, match='no longer authorized'):
        resolver({}, inputs, context)
    state['authorized'] = True
    Path(first.source['head']).write_bytes(b'replaced')
    with pytest.raises(PermissionError, match='materialization changed'):
        resolver({}, inputs, context)


def test_runtime_change_cannot_reuse_old_resident_identity(models):
    resolver, inputs, context, _ = models
    first = resolver({}, inputs, {**context, 'decision_materialize': True})
    resolver.engine.implementation = 'b' * 64
    with pytest.raises(PermissionError):
        resolver({}, inputs, context)
    second = resolver({}, inputs, {**context, 'decision_materialize': True})
    assert second.identity != first.identity and second.source['head'] != first.source['head']

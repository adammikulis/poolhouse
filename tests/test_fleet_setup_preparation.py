"""Setup selections use the same current installed inventory and fixed catalogue."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import write_gguf

from poolhouse.fleet import setup_preparation as preparation
from poolhouse.fleet.catalogue import Suggestion
from poolhouse.fleet.models import Model
from poolhouse.hub.probe import GIB, MachineMemory


def fixture_ui(tmp_path, monkeypatch):
    monkeypatch.setattr(preparation, 'machine_memory', lambda: MachineMemory(
        total_ram=32 * GIB, available_ram=24 * GIB, unified=True))
    monkeypatch.setattr(preparation, 'every_server', lambda: [])
    monkeypatch.setattr(preparation.startup_models, 'choices', lambda **kwargs: {
        'ok': True, 'context': 8192, 'memory': {'free_gb': 24}, 'models': [
            {'name': 'Curated', 'file': 'curated.gguf', 'gb': 1, 'memory_gb': 2, 'fits_now': True}]})
    monkeypatch.setattr(preparation, 'SUGGESTED', [Suggestion('Curated', 'hf:fixture/model/curated.gguf', 1, 'Text')])
    rows = []

    def add(name, *, chat=True, servable=True):
        path = tmp_path / name
        write_gguf(path, {'general.architecture': 'llama', 'llama.context_length': 4096,
                          'llama.block_count': 2, 'llama.embedding_length': 32,
                          'llama.attention.head_count': 2, 'llama.attention.head_count_kv': 2})
        rows.append({'name': name, 'path': str(path), 'size_bytes': path.stat().st_size,
                     'context_length': 4096, 'servable': servable, 'capabilities': {'chat': chat}})
        return path

    def model(path):
        row = next((row for row in rows if row['path'] == path and row['servable']), None)
        return Model(Path(path).name, Path(path), row['size_bytes'], Path(path).stat().st_mtime) if row else None

    ui = SimpleNamespace(models=SimpleNamespace(library=lambda: rows, library_model=model,
        free_gb=lambda: 100, find=lambda name: pytest.fail('loose model lookup forbidden')),
        serving=SimpleNamespace(live=lambda **kwargs: []))
    return ui, add, rows


def test_prefers_exact_running_then_installed_without_claiming_tool_readiness(tmp_path, monkeypatch):
    ui, add, _ = fixture_ui(tmp_path, monkeypatch)
    other = add('installed.gguf')
    running = add('running.gguf')
    ui.serving.live = lambda **kwargs: [SimpleNamespace(port=12345)]
    monkeypatch.setattr(preparation, 'every_server', lambda: [
        {'model': str(running), 'port': 12345, 'defunct': False},
        {'model': str(other), 'port': 9999, 'defunct': False}])
    result = preparation.choices_for(ui)
    assert result['models'][0]['file'] == 'running.gguf'
    assert result['models'][0]['running'] is True
    assert result['models'][0]['recommended'] is True
    assert [row['installed'] for row in result['models']] == [True, True, False]
    assert result['models'][0]['estimated_context'] == 4096
    assert result['models'][0]['download_bytes'] == 0
    assert result['memory_estimated'] is True
    assert not any('tools_ready' in row or 'chat_ready' in row for row in result['models'])


def test_substring_and_wrong_port_or_dead_process_never_mark_running(tmp_path, monkeypatch):
    ui, add, _ = fixture_ui(tmp_path, monkeypatch)
    path = add('chosen.gguf')
    ui.serving.live = lambda **kwargs: [SimpleNamespace(port=12345)]
    monkeypatch.setattr(preparation, 'every_server', lambda: [
        {'model': str(path) + '-other', 'port': 12345, 'defunct': False},
        {'model': str(path), 'port': 12345, 'defunct': True},
        {'model': str(path), 'port': 54321, 'defunct': False}])
    assert not any(row['running'] for row in preparation.choices_for(ui)['models'])


def test_nonchat_incomplete_and_unknown_capability_are_not_setup_choices(tmp_path, monkeypatch):
    ui, add, _ = fixture_ui(tmp_path, monkeypatch)
    add('embedding.gguf', chat=False)
    add('unknown.gguf', chat=None)
    add('incomplete.gguf', servable=False)
    assert [row['model_id'] for row in preparation.choices_for(ui)['models']] == ['curated.gguf']


def test_selected_installed_handle_refuses_replaced_artifact_and_raw_paths(tmp_path, monkeypatch):
    ui, add, _ = fixture_ui(tmp_path, monkeypatch)
    path = add('chosen.gguf')
    ident = preparation.choices_for(ui)['models'][0]['model_id']
    model, pick = preparation.selected_model(ui, ident)
    assert model.path == path and pick is None
    with path.open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError, match='changed'):
        preparation.selected_model(ui, ident)
    for value in (str(path), path.name, '../../chosen.gguf', True, None):
        with pytest.raises(ValueError):
            preparation.selected_model(ui, value)


def test_current_catalogue_only_no_second_download_of_installed_selection(tmp_path, monkeypatch):
    ui, add, _ = fixture_ui(tmp_path, monkeypatch)
    model, pick = preparation.selected_model(ui, 'curated.gguf')
    assert model is None and pick.ref == 'hf:fixture/model/curated.gguf'
    add('curated.gguf')
    choices = preparation.choices_for(ui)['models']
    assert len(choices) == 1 and choices[0]['installed'] is True
    with pytest.raises(ValueError, match='installed'):
        preparation.selected_model(ui, 'curated.gguf')
    with pytest.raises(ValueError):
        preparation.selected_model(ui, 'hf:attacker/model/curated.gguf')


def test_fit_unknown_is_not_recommended_as_ready(tmp_path, monkeypatch):
    ui, add, _ = fixture_ui(tmp_path, monkeypatch)
    add('chosen.gguf')
    monkeypatch.setattr(preparation, 'estimate', lambda *a: (_ for _ in ()).throw(ValueError('unknown metadata')))
    rows = preparation.choices_for(ui)['models']
    installed = next(row for row in rows if row['installed'])
    assert installed['fits_now'] is None and installed['memory_gb'] is None
    assert installed['recommended'] is False
    assert next(row for row in rows if row['recommended'])['model_id'] == 'curated.gguf'


def test_installed_replacement_during_inventory_resolution_denies(tmp_path, monkeypatch):
    ui, add, _ = fixture_ui(tmp_path, monkeypatch)
    path = add('chosen.gguf')
    ident = preparation.choices_for(ui)['models'][0]['model_id']
    original = ui.models.library_model

    def changed(selected):
        result = original(selected)
        with path.open('ab') as stream:
            stream.write(b'replaced during lookup')
        return result

    ui.models.library_model = changed
    with pytest.raises(ValueError, match='changed'):
        preparation.selected_model(ui, ident)


def test_resolver_cannot_return_another_current_inventory_model(tmp_path, monkeypatch):
    ui, add, _ = fixture_ui(tmp_path, monkeypatch)
    chosen = add('chosen.gguf')
    other = add('other.gguf')
    ident = next(row['model_id'] for row in preparation.choices_for(ui)['models'] if row['file'] == chosen.name)
    original = ui.models.library_model
    ui.models.library_model = lambda path: original(str(other))
    with pytest.raises(ValueError, match='changed'):
        preparation.selected_model(ui, ident)

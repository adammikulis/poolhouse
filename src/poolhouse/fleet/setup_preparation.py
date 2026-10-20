"""Exact first-run model choices; readiness requires a later observed serving proof."""
from pathlib import Path

from poolhouse.files import stat_signature
from poolhouse.hub.probe import machine_memory
from poolhouse.serve.estimate import Setup, estimate
from poolhouse.serve.process import every_server
from poolhouse.studio.store import digest

from . import model_components, startup_models
from .catalogue import SUGGESTED


def _installed(ui):
    if ui.models is None:
        return []
    return [row for row in ui.models.library()
            if row['servable'] is True and row['capabilities'].get('chat') is True]


def _model_id(row):
    path = Path(row['path']).resolve()
    effective = model_components.effective(path).resolve()
    parts = [path, effective]
    base = path if path.is_dir() else path.parent
    parts.extend(base / part['name'] for part in row.get('files', []))
    if path.is_dir():
        parts.extend(part for part in path.iterdir() if part.is_file())
    identity = [(str(part.resolve()), stat_signature(part.stat())) for part in sorted(set(parts))]
    return 'installed:' + digest({'path': str(path), 'identity': identity})


def _running(ui):
    if ui.serving is None:
        return set()
    live = {row.port for row in ui.serving.live(force=True)}
    return {str(Path(row['model']).resolve()) for row in every_server()
            if row['port'] in live and not row['defunct'] and Path(row['model']).is_absolute()}


def _estimate(row, context, free):
    try:
        model = model_components.effective(Path(row['path']))
        size = estimate(model, Setup(context=context)).total_bytes
    except (OSError, ValueError, KeyError):
        return {'memory_gb': None, 'fits_now': None}
    return {'memory_gb': round(size / 2**30, 1), 'fits_now': size < free * .95}


def choices_for(ui):
    """Authenticated metadata callers only; estimates never reserve compute or prove tools."""
    machine = machine_memory()
    installed = _installed(ui)
    running = _running(ui)
    current = startup_models.choices(machine=machine,
        disk_gb=ui.models.free_gb() if ui.models else 0, installed=())
    free = startup_models.memory_pool(machine)[2]
    rows = []
    for row in installed:
        path = str(Path(row['path']).resolve())
        maximum = row.get('context_length')
        context = min(startup_models.CONTEXT, maximum) if type(maximum) is int and maximum > 0 else startup_models.CONTEXT
        active = str(model_components.effective(Path(path)).resolve()) in running
        rows.append({'model_id': _model_id(row), 'name': row['name'], 'file': Path(path).name,
                     'installed': True, 'running': active, 'download_bytes': 0,
                     'size_bytes': row['size_bytes'], 'context_length': maximum,
                     'estimated_context': context, 'recommended': False,
                     **_estimate(row, context, free)})
    names = {Path(row['path']).name for row in installed}
    for row in current['models']:
        if row['file'] in names:
            continue
        rows.append({**row, 'model_id': row['file'], 'installed': False, 'running': False,
                     'download_bytes': int(row['gb'] * 2**30), 'recommended': False})
    rows.sort(key=lambda row: (not row['running'], not row['installed'], row['fits_now'] is not True,
                               row['memory_gb'] is None, -(row['memory_gb'] or 0), row['model_id']))
    preferred = next((row for row in rows if row['running']), None)
    if preferred is None:
        preferred = next((row for row in rows if row['fits_now'] is True), None)
    if preferred is not None:
        preferred['recommended'] = True
    return {**current, 'models': rows, 'memory_estimated': True}


def selected_model(ui, model_id):
    """Resolve only current server-owned choices, never a caller path or a loose name."""
    if not isinstance(model_id, str):
        raise ValueError('Choose a current setup model.')
    installed = _installed(ui)
    if model_id.startswith('installed:'):
        row = next((row for row in installed if _model_id(row) == model_id), None)
        model = ui.models.library_model(row['path']) if row is not None else None
        if (model is None or str(model.path.resolve()) != str(Path(row['path']).resolve())
                or _model_id({**row, 'path': str(model.path)}) != model_id):
            raise ValueError('The selected installed model changed. Select it again.')
        return model, None
    pick = next((pick for pick in SUGGESTED if pick.file == model_id), None)
    if pick is None:
        raise ValueError('Choose a current setup model.')
    if any(Path(row['path']).name == pick.file for row in installed):
        raise ValueError('Select the current installed model instead of downloading it again.')
    offered = choices_for(ui)
    if not any(row['model_id'] == model_id for row in offered['models']):
        raise ValueError('This setup model is not available for this device.')
    return None, pick

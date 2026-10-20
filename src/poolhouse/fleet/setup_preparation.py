"""Exact first-run choices and independently authorized historical job inspection."""
import copy
import re
from pathlib import Path

from poolhouse.files import stat_signature
from poolhouse.hub.probe import machine_memory
from poolhouse.serve.estimate import Setup, estimate
from poolhouse.serve.process import every_server
from poolhouse.studio.store import digest

from . import model_components, setup_jobs, startup_models
from .catalogue import SUGGESTED


class Preparation:
    """Private setup service. Callers supply actual current strong-owner proofs.

    Inspection never adopts the original principal's execution or cancellation
    authority. Completed evidence remains historical until a resident startup
    receipt can independently prove the current loaded implementation.
    """

    def __init__(self, ui):
        self.ui = ui

    @staticmethod
    def _check(binding, current):
        if (not isinstance(binding, dict) or set(binding) != {'scope', 'device', 'principal'}
                or any(not isinstance(value, str) or not value or len(value) > 256
                       for value in binding.values())):
            raise ValueError('Invalid setup context.')
        if not callable(current) or current() is not True:
            raise PermissionError('Current setup access is required.')

    def _row(self, ident, binding, current):
        self._check(binding, current)
        if not isinstance(ident, str) or re.fullmatch(r'[a-f0-9]{32}', ident) is None:
            raise ValueError('Invalid preparation identifier.')
        row = setup_jobs.jobs(self.ui).get(ident)
        self._check(binding, current)
        original = row.get('request', {}).get('binding', {}) if row else {}
        if (row is None or row['kind'] != setup_jobs.PREPARATION
                or any(original.get(key) != binding[key] for key in ('scope', 'device'))):
            raise KeyError(ident)
        self._check(binding, current)
        return row

    @staticmethod
    def _public(row, *, binding):
        result = row.get('result', {})
        model_id = result.get('model_id')
        safe = {}
        if isinstance(model_id, str) and (re.fullmatch(r'installed:[a-f0-9]{64}', model_id)
                                         or any(pick.file == model_id for pick in SUGGESTED)):
            safe['model_id'] = model_id
        fingerprint = result.get('artifact_digest')
        if isinstance(fingerprint, str) and re.fullmatch(r'[a-f0-9]{64}', fingerprint):
            safe['artifact_digest'] = fingerprint
        serving = result.get('serving')
        if isinstance(serving, dict):
            safe['serving'] = {key: serving[key] for key in ('port', 'context', 'slot_context')
                               if type(serving.get(key)) is int and 0 < serving[key] <= (
                                   65535 if key == 'port' else 2**31 - 1)}
        transfer = result.get('transfer')
        if (isinstance(transfer, dict) and transfer.get('owner') in ('setup-job', 'shared-download')
                and isinstance(transfer.get('id'), str)
                and re.fullmatch(r'(?:[a-f0-9]{32}|[0-9]{1,20}-[a-f0-9]{6})', transfer['id'])
                and all(type(transfer.get(key)) is int and transfer[key] >= 0 for key in ('done', 'total'))):
            safe['transfer'] = {key: transfer[key] for key in ('owner', 'id', 'done', 'total')}
        safe.update(chat_ready=False, tools_ready=False, historical=True)
        can_cancel = (row['request']['binding'] == binding and row['state'] in setup_jobs.ACTIVE
                      and row['cancel_requested'] is False)
        return {**{key: copy.deepcopy(row[key]) for key in (
            'id', 'kind', 'state', 'stage', 'progress', 'cancel_requested', 'created', 'note', 'error')
                   if key in row}, 'result': safe, 'can_cancel': can_cancel}

    def status(self, ident, *, binding, current):
        row = self._row(ident, binding, current)
        result = self._public(row, binding=binding)
        self._check(binding, current)
        return result

    def cancel(self, ident, *, binding, current):
        row = self._row(ident, binding, current)
        if row['request']['binding'] != binding:
            raise PermissionError('Only the original current setup session may cancel preparation.')
        row = setup_jobs.jobs(self.ui).cancel_preparation(ident, binding=binding, current=current)
        result = self._public(row, binding=binding)
        self._check(binding, current)
        return result


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

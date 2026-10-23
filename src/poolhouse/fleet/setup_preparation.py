"""Exact first-run choices and independently authorized historical job inspection."""
import copy
import re
import threading
import time
from dataclasses import replace
from pathlib import Path

from poolhouse.files import stat_signature
from poolhouse.hub.probe import machine_memory
from poolhouse.serve.broker import BrokerError
from poolhouse.serve.estimate import Setup, estimate
from poolhouse.serve.process import every_server
from poolhouse.serve.suggest import largest_fit
from poolhouse.studio.store import digest

from . import llama, model_components, setup_jobs, setup_probe, startup_models
from .catalogue import SUGGESTED
from .models import ModelTransfer


class Preparation:
    """Private setup service. Callers supply actual current strong-owner proofs.

    Inspection never adopts the original principal's execution or cancellation
    authority. Completed evidence remains historical until a resident startup
    receipt can independently prove the current loaded implementation.
    """

    def __init__(self, ui):
        self.ui = ui
        self._mutex = threading.RLock()
        self._controls = {}

    def start(self, body, *, binding, current):
        self._check(binding, current)
        selection = self._selection(body)
        jobs = setup_jobs.jobs(self.ui)
        request = {'idempotency_key': body['idempotency_key'], 'binding': dict(binding), 'selection': selection}
        for row in jobs.all():
            original = row.get('request', {})
            if (row['kind'] == setup_jobs.PREPARATION and original.get('binding') == binding
                    and original.get('idempotency_key') == body['idempotency_key']):
                if original != request:
                    raise ValueError('This preparation key already identifies a different selection.')
                return self.status(row['id'], binding=binding, current=current)
        model, _ = selected_model(self.ui, selection['model_id'])
        if model is None and not selection['download']:
            raise ValueError('Allow the selected model download before preparing it.')
        self._project(selection, current)
        self._check(binding, current)
        row = jobs.prepare(request, lambda progress: self._execute(selection, progress), current=current)
        return self.status(row['id'], binding=binding, current=current)

    @staticmethod
    def _selection(body):
        if (not isinstance(body, dict) or not {'idempotency_key', 'model_id', 'download'} <= set(body)
                or set(body) - {'idempotency_key', 'model_id', 'download', 'project_id'}
                or not isinstance(body['idempotency_key'], str)
                or re.fullmatch(r'[a-f0-9]{32}', body['idempotency_key']) is None
                or not isinstance(body['model_id'], str) or not 0 < len(body['model_id']) <= 512
                or type(body['download']) is not bool
                or ('project_id' in body and (not isinstance(body['project_id'], str)
                    or re.fullmatch(r'[a-f0-9]{32}', body['project_id']) is None))):
            raise ValueError('Invalid preparation selection.')
        return {key: body[key] for key in ('model_id', 'download', 'project_id') if key in body}

    def _project(self, selection, current):
        if 'project_id' in selection:
            self.ui.projects.resolve_coding_project(selection['project_id'], current=current)

    def _execute(self, selection, progress):
        control = setup_probe.Control(progress.local_check)
        with self._mutex:
            self._controls[progress.row['id']] = control
        try:
            progress.local_check()
            model = selected_model(self.ui, selection['model_id'])[0] if selection['model_id'].startswith('installed:') else None
            self._project(selection, progress.local_check)
            progress.stage('runtime')
            target = self._resident(model, progress) if model is not None else None
            transfer = {'owner': 'setup-job', 'id': progress.row['id'], 'done': 0, 'total': 0}
            if target is None:
                progress.check()
                # Installation is indivisible/shared; cancellation never undoes it.
                llama.ensure_server(self.ui.root, on_progress=lambda _: progress.local_check(),
                                    sources=self.ui.settings.download_sources if self.ui.settings else 'both')
                progress.check()
                progress.stage('transfer')
                model, transfer = self._obtain(selection, progress)
                progress.stage('verify')
                path = model_components.effective(model.path).resolve(strict=True)
                fingerprint = self.ui.models.digest(replace(model, path=path), check=progress.local_check)
                reference = model.verified_reference
                if not selection['model_id'].startswith('installed:') and (
                        reference is None or reference.sha256 != fingerprint):
                    raise ValueError('The selected model changed from its verified reference.')
                progress.check()
                self._project(selection, progress.local_check)
                progress.stage('serve')
                served = self.ui.start_serving(model, context=self._context(model), current=progress.local_check)
                progress.check()
                if type(served.context) is not int or served.context <= 0:
                    raise setup_jobs.PreparationStopped('The current server context could not be verified.')
                target = {'model': str(path), 'artifact_digest': fingerprint,
                          'port': served.port, 'context': served.context}
            observed = setup_probe.observe(target, progress, control)
            if observed['chat_ready'] is True:
                progress.stage('ready')
            return {**observed, 'model_id': selection['model_id'], 'artifact_digest': target['artifact_digest'],
                    'serving': {'port': target['port'], 'context': target['context']}, 'transfer': transfer}
        finally:
            control.set()
            with self._mutex:
                self._controls.pop(progress.row['id'], None)

    def _resident(self, model, progress):
        if getattr(self.ui, 'serving', None) is None:
            return None
        path = model_components.effective(model.path).resolve(strict=True)
        fingerprint = self.ui.models.digest(replace(model, path=path), check=progress.local_check)
        progress.check()
        for served in self.ui.serving.live(force=True):
            if (type(served.port) is not int or not 0 < served.port < 65536
                    or type(served.context) is not int or served.context <= 0):
                continue
            target = {'model': str(path), 'artifact_digest': fingerprint,
                      'port': served.port, 'context': served.context}
            progress.check()
            try:
                receipt = setup_probe.checked_startup(target)
            except (BrokerError, OSError):
                progress.check()
                continue
            progress.check()
            if receipt is not None:
                return target
        return None

    def _context(self, model):
        row = next((row for row in self.ui.models.library() if row['path'] == str(model.path)), None)
        if row is None:
            raise ValueError('The selected installed model changed before starting.')
        maximum = row.get('context_length')
        default = int(getattr(self.ui.settings, 'context', 0) or 0)
        # no preference saved: None, and the start fits the model (`fitted_settings`)
        return min(default, maximum) if default and type(maximum) is int and maximum > 0 else None

    def _obtain(self, selection, progress):
        if selection['model_id'].startswith('installed:'):
            model, _ = selected_model(self.ui, selection['model_id'])
            return model, {'owner': 'setup-job', 'id': progress.row['id'], 'done': 0, 'total': 0}
        pick = next(pick for pick in SUGGESTED if pick.file == selection['model_id'])
        if not selection['download']:
            raise ValueError('The selected model download was not authorized.')
        getting = self.ui.downloads.active() if self.ui.downloads is not None else []
        shared = next((row for row in getting if row.name == pick.file and row.state == 'getting'), None)
        if shared is not None and (shared.source != pick.ref or shared.components):
            raise ValueError('The selected model has a different download in progress.')
        last = [0.0, 0, 0]

        def report(done, total):
            progress.local_check()
            now = time.monotonic()
            last[1:] = done, total
            if now - last[0] >= 1 or (total and done == total):
                progress.stage('transfer', done=done, total=total)
                last[0] = now

        while shared is not None and shared.state == 'getting':
            report(shared.done, shared.total)
            time.sleep(.1)
        progress.check()
        if shared is not None and shared.state != 'done':
            raise ValueError('The shared model download did not complete.')
        model = self.ui.models.ensure(pick.file, source=pick.ref, key=None,
            transfer=ModelTransfer(progress=report, current=progress.local_check), autodownload=True)
        progress.check()
        # A concurrent ordinary download may have finished under the same target lock.
        found = [row for row in _installed(self.ui) if Path(row['path']).name == pick.file]
        if len(found) != 1 or Path(found[0]['path']).resolve() != model.path.resolve():
            raise ValueError('The downloaded model is not a complete current library selection.')
        reference = model.verified_reference
        if reference is None or reference.source != pick.ref:
            raise ValueError('The selected model has no verified source reference.')
        model = self.ui.models.library_model(found[0]['path'])
        if model is None:
            raise ValueError('The selected model is no longer a complete current library selection.')
        return replace(model, verified_reference=reference), {'owner': 'shared-download' if shared else 'setup-job',
                       'id': shared.id if shared else progress.row['id'], 'done': last[1], 'total': last[2]}

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
        self._ready(row, result, current)
        self._check(binding, current)
        return result

    def cancel(self, ident, *, binding, current):
        row = self._row(ident, binding, current)
        if row['request']['binding'] != binding:
            raise PermissionError('Only the original current setup session may cancel preparation.')
        row = setup_jobs.jobs(self.ui).cancel_preparation(ident, binding=binding, current=current)
        with self._mutex:
            control = self._controls.get(ident)
        if control is not None:
            control.set()
        result = self._public(row, binding=binding)
        self._check(binding, current)
        return result

    def _ready(self, row, public, current):
        proof = row.get('serving_receipt')
        probes = row.get('probes', {})
        if (not proof or row['cancel_requested'] or row['state'] not in ('done', 'interrupted')
                or probes.get('chat', {}).get('status') != 'accepted'
                or probes['chat'].get('startup_id') != proof['startup_id']):
            return
        models = _installed(self.ui)
        for model in models:
            path = str(model_components.effective(Path(model['path'])).resolve())
            if current() is not True:
                raise PermissionError('Current setup access is required.')
            try:
                observed = setup_probe.checked_startup({'port': proof['port'], 'context': proof['context'],
                    'model': path, 'artifact_digest': proof['artifact_digest']})
            except (BrokerError, OSError):
                if current() is not True:
                    raise PermissionError('Current setup access is required.') from None
                return
            if current() is not True:
                raise PermissionError('Current setup access is required.')
            if (observed is None or observed['id'] != proof['startup_id']
                    or observed['runtime']['binary']['sha256'] != proof['runtime_digest']
                    or observed['runtime']['parser'] != proof['parser']):
                continue
            if current() is not True:
                raise PermissionError('Current setup access is required.')
            public['result'].update(model=model['path'], chat_ready=True, historical=False,
                tools_ready=all(probes.get(phase, {}).get('status') == 'accepted'
                                and probes[phase].get('startup_id') == proof['startup_id']
                                for phase in setup_jobs.PROBES))
            return


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


def _fitted_context(row, maximum):
    """The largest context this machine holds for an installed model; 0 (none claimed) when its
    header cannot be fitted."""
    try:
        return largest_fit(model_components.effective(Path(row['path']))).context
    except (OSError, ValueError):
        return 0


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
        context = _fitted_context(row, maximum)
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


def service(ui):
    """One preparation owner per actual UI, without work during construction."""
    with ui._setup_jobs_lock:
        if getattr(ui, '_setup_preparation', None) is None:
            ui._setup_preparation = Preparation(ui)
        return ui._setup_preparation

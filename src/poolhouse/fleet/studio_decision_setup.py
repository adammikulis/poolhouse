"""Explicit one-click setup for the one compiled Studio decision model."""
from __future__ import annotations

from dataclasses import asdict

from poolhouse.decide.fetch import ENDPOINT, locate
from poolhouse.decide.pins import STRANDS_V19
from poolhouse.redact.secrets import redact
from poolhouse.studio.decision_inventory import RESOURCE_ID, resource
from poolhouse.studio.store import digest

from .setup_jobs import jobs, provenance

REQUIRED = frozenset({'torch', 'transformers', 'peft', 'safetensors'})


def _authorized(engine, principal):
    if not principal.get('owner') or not engine.authorize(principal, None, {}):
        raise PermissionError('Sign in with setup access to configure the decision model.')


def _sources(ui):
    if ui.settings is not None and ui.settings.download_sources not in ('internet', 'both'):
        raise PermissionError('Enable Internet or Both download sources to configure the decision model.')


def ready(ui) -> bool:
    environment = ui.environment
    if environment is None or not environment.exists:
        return False
    try:
        environment.require_current_runtime()
        environment.require_compute()
        return environment.installed().keys() >= REQUIRED and bool(resource())
    except (ValueError, OSError, RuntimeError):
        return False


def _install(ui, engine, principal, progress):
    _authorized(engine, principal)
    _sources(ui)
    if ready(ui):
        return {'ok': True, 'ready': True, 'resource': RESOURCE_ID}
    try:
        progress('Installing decision model libraries')
        changed = ui.environment.install(['core', 'decide-pointer'], on_progress=progress)
        if any(not result.get('ok') for result in changed.values()):
            errors = '; '.join(str(value.get('error', 'Installation failed')) for value in changed.values() if not value.get('ok'))
            raise RuntimeError(errors)
        for index, pin in enumerate(STRANDS_V19.files, 1):
            _authorized(engine, principal)
            _sources(ui)
            progress(f'Downloading and verifying model asset {index} of {len(STRANDS_V19.files)}')
            locate(pin, download=True, endpoint=ENDPOINT)
        if not ready(ui):
            raise RuntimeError('The decision model setup could not verify all required files and libraries.')
    except (ValueError, OSError, RuntimeError) as error:
        raise RuntimeError(redact(str(error))[0][:2000]) from None
    return {'ok': True, 'ready': True, 'resource': RESOURCE_ID}


def configure(route, engine, principal) -> tuple[int, dict]:
    """Accept no caller-selected resources, packages, URLs, paths or execution code."""
    if route.body() != {}:
        raise ValueError('The decision model setup action accepts no configuration fields.')
    ui = route.ui
    _authorized(engine, principal)
    if ui.environment is None or ui.root is None:
        raise ValueError('The managed runtime is unavailable on this device.')
    if ready(ui):
        return 200, {'ok': True, 'ready': True}
    _sources(ui)
    request = {'resource': RESOURCE_ID, 'pins': digest([asdict(pin) for pin in STRANDS_V19.files]),
               'runtime': engine.implementation, 'scope': principal['scope'], 'principal': principal['id']}
    job = jobs(ui).start('studio-decision-install', request,
                         lambda progress: _install(ui, engine, principal, progress), provenance=provenance(route))
    return 202, {'ok': True, 'ready': False, 'job': job}

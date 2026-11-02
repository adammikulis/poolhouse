"""Explicit fixed model setup uses only the maintained library and pin installers."""
import threading
from types import SimpleNamespace

import pytest

from poolhouse.fleet import studio_decision_setup as setup


@pytest.fixture
def configured_route(tmp_path, monkeypatch):
    state = {'authorized': True, 'installed': False, 'pins': 0, 'calls': []}
    def install(names, *, on_progress):
        state['calls'].append(('libraries', names))
        state['installed'] = True
        return {name: {'ok': True} for name in names}
    environment = SimpleNamespace(exists=True, require_current_runtime=lambda: None,
                                  require_compute=lambda: 'verified',
                                  installed=lambda: dict.fromkeys(setup.REQUIRED, '1') if state['installed'] else {}, install=install)
    ui = SimpleNamespace(root=tmp_path, environment=environment, settings=SimpleNamespace(download_sources='both'),
                         _setup_jobs_lock=threading.RLock(), setup_jobs=None)
    route = SimpleNamespace(ui=ui, body=lambda: {}, cookie='', client_ip='127.0.0.1')
    engine = SimpleNamespace(implementation='frozen-runtime', authorize=lambda *_: state['authorized'])
    principal = {'id': 'current-session', 'scope': 'current-pool', 'owner': True}
    def resource():
        if state['pins'] != len(setup.STRANDS_V19.files):
            raise ValueError('missing pins')
        return {'id': setup.RESOURCE_ID}
    def locate(pin, *, download, endpoint):
        assert pin in setup.STRANDS_V19.files and download and endpoint == setup.ENDPOINT
        state['calls'].append(('pin', pin.filename))
        state['pins'] += 1
    monkeypatch.setattr(setup, 'resource', resource)
    monkeypatch.setattr(setup, 'locate', locate)
    yield route, engine, principal, state
    if ui.setup_jobs and (worker := ui.setup_jobs._worker):
        worker.join(5)
        assert not worker.is_alive()


def finish(ui):
    if worker := ui.setup_jobs._worker:
        worker.join(5)
        assert not worker.is_alive()
    return ui.setup_jobs.all()[-1]


def test_explicit_configure_installs_only_fixed_dependencies_and_pins(configured_route):
    route, engine, principal, state = configured_route
    status, reply = setup.configure(route, engine, principal)
    assert status == 202 and not reply['ready']
    completed = finish(route.ui)
    assert completed['state'] == 'done'
    assert setup.ready(route.ui)
    assert state['calls'][0] == ('libraries', ['core', 'decide-pointer'])
    assert len(state['calls']) == 1 + len(setup.STRANDS_V19.files)
    prior = list(state['calls'])
    assert setup.configure(route, engine, principal) == (200, {'ok': True, 'ready': True})
    assert state['calls'] == prior and len(route.ui.setup_jobs.all()) == 1


def test_duplicate_click_attaches_pending_install(configured_route):
    route, engine, principal, _ = configured_route
    started, release = threading.Event(), threading.Event()
    original = route.ui.environment.install
    def wait(names, *, on_progress):
        started.set()
        assert release.wait(5)
        return original(names, on_progress=on_progress)
    route.ui.environment.install = wait
    try:
        _, first = setup.configure(route, engine, principal)
        assert started.wait(5)
        _, second = setup.configure(route, engine, principal)
        assert first['job']['id'] == second['job']['id']
    finally:
        release.set()
    assert finish(route.ui)['state'] == 'done'
    assert len(route.ui.setup_jobs.all()) == 1


@pytest.mark.parametrize('body', [{'url': 'https://untrusted.invalid'}, {'packages': ['untrusted']}, {'argv': ['untrusted']}])
def test_configure_cannot_select_download_or_execution(configured_route, body):
    route, engine, principal, state = configured_route
    route.body = lambda: body
    with pytest.raises(ValueError, match='no configuration fields'):
        setup.configure(route, engine, principal)
    assert not state['calls'] and route.ui.setup_jobs is None


def test_download_policy_and_current_setup_access_are_required(configured_route):
    route, engine, principal, state = configured_route
    route.ui.settings.download_sources = 'pool'
    with pytest.raises(PermissionError, match='download sources'):
        setup.configure(route, engine, principal)
    route.ui.settings.download_sources = 'both'
    state['authorized'] = False
    with pytest.raises(PermissionError, match='setup access'):
        setup.configure(route, engine, principal)
    assert not state['calls']


def test_revocation_between_libraries_and_model_fetch_stops_new_downloads(configured_route):
    route, engine, principal, state = configured_route
    original = route.ui.environment.install
    def revoke(names, *, on_progress):
        result = original(names, on_progress=on_progress)
        state['authorized'] = False
        return result
    route.ui.environment.install = revoke
    setup.configure(route, engine, principal)
    assert finish(route.ui)['state'] == 'failed'
    assert state['pins'] == 0


def test_install_failure_redacts_credentials_and_never_claims_ready(configured_route):
    route, engine, principal, state = configured_route
    secret = 'hf_' + 'x' * 35
    route.ui.environment.install = lambda *_args, **_kwargs: {'decide-pointer': {'ok': False, 'error': secret}}
    setup.configure(route, engine, principal)
    result = finish(route.ui)
    assert result['state'] == 'failed' and secret not in result['error']
    assert not setup.ready(route.ui) and state['pins'] == 0

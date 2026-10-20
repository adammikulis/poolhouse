"""Actual setup HTTP authorization and durable receipts, with compute held off."""
import http.client
import json
from types import SimpleNamespace

import pytest
from launch_support import browser as browser_headers
from test_fleet_initial_setup import device, initial

from poolhouse import lock
from poolhouse.fleet import setup_context, setup_preparation
from poolhouse.fleet.models import Models
from poolhouse.fleet.session import COOKIE, parse_cookie

__all__ = ['device']
pytestmark = pytest.mark.redteam
PREFIX = '/ui/setup/prepare'


@pytest.fixture
def preparing(device, tmp_path, monkeypatch):
    root = tmp_path / 'setup-state'
    root.mkdir(mode=0o700)
    device.ui.root = root
    status, _, headers = initial(device)
    assert status == 200
    cookie = headers['Set-Cookie'].split(';')[0]
    store = tmp_path / 'models'
    store.mkdir()
    model = store / 'fixture.gguf'
    model.write_bytes(b'GGUF-fixture-metadata-only')
    info = model.stat()
    row = {'path':str(model), 'name':model.name, 'servable':True, 'capabilities':{'chat':True},
           'size_bytes':info.st_size, 'mtime':info.st_mtime, 'context_length':2048, 'files':[]}
    device.ui.models = Models(store=store, roots=[store])
    monkeypatch.setattr(device.ui.models, 'library', lambda: [row])
    monkeypatch.setattr(setup_preparation.llama, 'ensure_server', lambda *a, **k: pytest.fail('Unexpected runtime installation'))
    case = SimpleNamespace(device=device, cookie=cookie,
                           body={'model_id':setup_preparation._model_id(row), 'download':False,
                                 'idempotency_key':'a' * 32})
    with lock.held(root / 'setup-install.lock'):
        try:
            yield case
        finally:
            with device.ui.sessions._lock:
                device.ui.sessions._sessions.clear()
    jobs = device.ui.setup_jobs
    worker = jobs._worker if jobs is not None else None
    if worker is not None:
        worker.join(timeout=5)
        assert not worker.is_alive()


def call(case, path=PREFIX, *, method='POST', body=None, headers=None, **options):
    sent = browser_headers(case.device) if method == 'POST' else {'Sec-Fetch-Site':'same-origin'}
    return case.device.call(path, method=method, body=body, headers={**sent, **(headers or {})},
                            cookie=options.pop('cookie', case.cookie), **options)


def start(case):
    status, row, headers = call(case, body=case.body)
    assert status == 200, row
    assert headers['Cache-Control'] == 'no-store'
    return row


def raw(case, text):
    connection = http.client.HTTPConnection('127.0.0.1', case.device.port, timeout=10)
    try:
        connection.request('POST', PREFIX, text, {'Content-Type':'application/json', 'X-Poolhouse-UI':'1',
                           'Cookie':case.cookie, **browser_headers(case.device)})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def test_actual_originless_status_and_duplicate_submission_share_one_job(preparing):
    case = preparing
    first = start(case)
    assert start(case)['id'] == first['id']
    assert len(case.device.ui.setup_jobs.all()) == 1
    status, current, headers = call(case, PREFIX + '/' + first['id'], method='GET')
    assert status == 200 and current['id'] == first['id']
    assert current['can_cancel'] is True and current['result']['chat_ready'] is False
    assert 'binding' not in json.dumps(current) and 'idempotency_key' not in json.dumps(current)
    assert headers['Cache-Control'] == 'no-store'
    assert call(case, PREFIX + '/' + first['id'] + '/cancel', body={})[1]['can_cancel'] is False
    assert call(case, PREFIX + '/' + first['id'] + '/cancel', body={})[0] == 200
    assert len(case.device.ui.setup_jobs.all()) == 1


@pytest.mark.parametrize('headers', [
    {'Origin':'null'}, {'Origin':'http://foreign.invalid'}, {'Sec-Fetch-Site':'cross-site'},
    {'Authorization':'Bearer agent'}, {'X-Poolhouse-Token':'agent'}, {'X-Poolhouse-Agent':'agent'},
    {'X-Poolhouse-UI':'0'}, {'Host':'[malformed'}, {'Host':'127.0.0.1:1'},
    {'Host':'present@127.0.0.1:1'}, {'Host':'127.0.0.1:1/path'},
])
def test_status_denies_foreign_headers_before_loading_jobs(preparing, headers):
    status, _, _ = call(preparing, PREFIX + '/' + 'c' * 32, method='GET', headers=headers)
    assert status == 403
    assert preparing.device.ui.setup_jobs is None


def test_originless_get_never_admits_originless_post_or_head(preparing):
    assert call(preparing, method='POST', body=preparing.body,
                headers={'Origin':'', 'Sec-Fetch-Site':'same-origin'})[0] == 403
    assert call(preparing, PREFIX + '/' + 'c' * 32, method='HEAD')[0] == 405
    assert call(preparing, PREFIX + '/' + 'c' * 32, method='GET', ui_header=False)[0] == 403
    assert preparing.device.ui.setup_jobs is None


@pytest.mark.parametrize('kind', ['missing', 'forged', 'expired', 'noncredentialed', 'token'])
def test_status_does_not_confuse_lost_sessions_with_credential_authority(preparing, kind):
    ui = preparing.device.ui
    if kind == 'expired':
        ui.sessions.get(parse_cookie(preparing.cookie)).expires_at = 0
        cookie = preparing.cookie
    elif kind in {'noncredentialed', 'token'}:
        held = ui.sessions.open('token' if kind == 'token' else 'browser', 'token' if kind == 'token' else '')
        cookie = ui.sessions.cookie_header(held).split(';')[0]
    else:
        cookie = '' if kind == 'missing' else COOKIE + '=forged'
    status, _, _ = call(preparing, PREFIX + '/' + 'c' * 32, method='GET', cookie=cookie)
    assert status == (403 if kind in {'noncredentialed', 'token'} else 401)
    assert ui.setup_jobs is None


def test_new_current_owner_can_inspect_but_cannot_cancel_an_original_request(preparing):
    first = start(preparing)
    ui = preparing.device.ui
    session = ui.sessions.open('setup', 'launch-ticket')
    cookie = ui.sessions.cookie_header(session).split(';')[0]
    status, row, _ = call(preparing, PREFIX + '/' + first['id'], method='GET', cookie=cookie)
    assert status == 200 and row['can_cancel'] is False
    assert row['result']['historical'] is True and 'model' not in row['result']
    assert call(preparing, PREFIX + '/' + first['id'] + '/cancel', body={}, cookie=cookie)[0] == 403
    assert preparing.device.ui.setup_jobs.get(first['id'])['cancel_requested'] is False


@pytest.mark.parametrize('body', [
    {}, {'download':1}, {'download':float('nan')}, {'model_id':False},
    {'project_id':'../outside'}, {'project_id':True}, {'source':'file:///outside'},
])
def test_malformed_selection_never_enqueues_work(preparing, body):
    selection = {} if not body else {**preparing.body, **body}
    assert call(preparing, body=selection)[0] == 400
    assert preparing.device.ui.setup_jobs is None


@pytest.mark.parametrize('text', [
    'null', '[]', '"text"', ' ' * 65537,
    '{"idempotency_key":"a","idempotency_key":"b","model_id":"x","download":false}',
    '{"idempotency_key":"a","model_id":"x","download":false,"project_id":"x","project_id":"y"}',
])
def test_nonobjects_duplicates_and_oversized_body_are_rejected(preparing, text):
    assert raw(preparing, text)[0] == 400
    assert preparing.device.ui.setup_jobs is None


def test_cancel_rejects_options_and_changed_idempotent_selection(preparing):
    first = start(preparing)
    assert call(preparing, body={**preparing.body, 'download':True})[0] == 400
    assert call(preparing, PREFIX + '/' + first['id'] + '/cancel', body={'force':True})[0] == 400
    assert not preparing.device.ui.setup_jobs.get(first['id'])['cancel_requested']


def test_current_context_revocation_during_status_cannot_publish_response(preparing, monkeypatch):
    first = start(preparing)
    service = setup_preparation.service(preparing.device.ui)
    public = service._public
    def revoke(row, *, binding):
        result = public(row, binding=binding)
        preparing.device.ui.sessions.get(parse_cookie(preparing.cookie)).expires_at = 0
        return result
    monkeypatch.setattr(service, '_public', revoke)
    assert call(preparing, PREFIX + '/' + first['id'], method='GET')[0] == 403


def test_pool_context_change_invalidates_captured_execution_callback(preparing):
    from poolhouse.fleet.discovery import mint_pool
    from poolhouse.fleet.routes import Router
    handler = SimpleNamespace(path=PREFIX, command='POST', client_address=('127.0.0.1', 1),
                              server=preparing.device.httpd,
                              headers={'Cookie':preparing.cookie, 'Host':f'127.0.0.1:{preparing.device.port}',
                                       'X-Poolhouse-UI':'1', **browser_headers(preparing.device)})
    context = setup_context.bind(Router(preparing.device.ui, handler))
    assert context.current() is True
    mint_pool('another-test-pool', preparing.device.keyfile, mode='dev')
    assert context.current() is False


def test_explicit_zero_host_port_is_not_treated_as_missing(preparing):
    from poolhouse.fleet.routes import Router
    handler = SimpleNamespace(path=PREFIX + '/' + 'c' * 32, command='GET',
                              client_address=('127.0.0.1', 1),
                              server=SimpleNamespace(server_address=('127.0.0.1', 80)),
                              headers={'Cookie':preparing.cookie, 'Host':'localhost:0',
                                       'X-Poolhouse-UI':'1', 'Sec-Fetch-Site':'same-origin'})
    assert Router(preparing.device.ui, handler)._local_browser(read=True) is False


def test_model_choices_rechecks_the_actual_session_after_metadata_read(preparing, monkeypatch):
    from poolhouse.fleet import routes
    def revoke(ui):
        ui.sessions.get(parse_cookie(preparing.cookie)).expires_at = 0
        return {'models':[{'model_id':'private-fixture'}]}
    monkeypatch.setattr(routes, 'choices_for', revoke)
    status, body, _ = call(preparing, '/ui/models/startup', method='GET')
    assert status == 401 and 'models' not in body

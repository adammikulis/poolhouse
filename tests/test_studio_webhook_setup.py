"""Configured synthetic sender crosses real isolated TLS ingress without credential export."""
import copy

import pytest
from test_studio_stream_http import served  # noqa: F401
from test_studio_webhooks import _serve

from poolhouse.fleet import studio_epoch


@pytest.fixture
def configured(request, tmp_path):
    app, cookie, session = request.getfixturevalue('served')
    app.call('/ui/studio/sources', cookie=cookie)
    server, thread, _ = _serve(tmp_path, app.ui, 'unused')
    app.ui.studio_endpoint = lambda: f'https://127.0.0.1:{server.server_port}'
    try:
        yield app, cookie, session
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def create(app, cookie):
    status, body, _ = app.call('/ui/studio/sources', method='POST', cookie=cookie,
        body={'kind': 'webhook', 'provider': 'local-synthetic', 'label': 'Synthetic secure source', 'idempotency_key': 'source'})
    assert status == 201, body
    source = body['source']
    assert source['available'] and source['test_supported'] and 'secret' not in str(source)
    status, body, _ = app.call('/ui/studio/workflows', method='POST', cookie=cookie,
        body={'title': 'Webhook records', 'graph': {'nodes': [{'id': 'record', 'type': 'stream.record'}], 'edges': []}})
    workflow = body['workflow']
    status, body, _ = app.call('/ui/studio/subscriptions', method='POST', cookie=cookie,
        body={'workflow_id': workflow['id'], 'revision': workflow['revision'], 'source_id': source['id'],
              'input_node': 'record', 'output_nodes': ['record'], 'destinations': [{'kind': 'live'}], 'idempotency_key': 'sub'})
    assert status == 201, body
    sub = body['subscription']
    status, body, _ = app.call('/ui/studio/subscriptions/' + sub['id'] + '/start', method='POST', cookie=cookie,
        body={'revision': sub['revision'], 'idempotency_key': 'start'})
    assert status == 200, body
    return source, body['subscription']


def send(app, cookie, source, sub, **options):
    return app.call('/ui/studio/subscriptions/' + sub['id'] + '/test-webhook', method='POST', cookie=cookie,
        body={'sample_id': options.get('sample') or source['default_sample_id'], 'idempotency_key': options.get('key', 'one')})


def test_actual_tls_sender_receipt_duplicate_and_changed_sample(configured):
    app, cookie, _ = configured
    source, sub = create(app, cookie)
    status, body, _ = send(app, cookie, source, sub)
    assert status == 202, body
    ident = body['receipt']['id']
    status, body, _ = send(app, cookie, source, sub)
    assert status == 202 and body['receipt']['id'] == ident
    status, body, _ = send(app, cookie, source, sub, sample=source['samples'][1]['id'])
    assert status == 409, body
    service = app.ui.studio_streams
    actual = service.store.get('subscription', sub['id'], app.ui.studio_authority._context()['scope'])
    assert actual['next_sequence'] == 2


def test_unknown_never_retransmits_or_accepts_new_key_after_reload(configured, monkeypatch):
    app, cookie, _ = configured
    source, sub = create(app, cookie)
    setup = app.ui.studio_webhook_setup
    calls = []
    def lost(*args):
        calls.append(True)
        raise OSError('lost')
    monkeypatch.setattr(setup, '_send', lost)
    assert send(app, cookie, source, sub)[0] == 409
    assert send(app, cookie, source, sub)[0] == 409
    assert send(app, cookie, source, sub, key='new')[0] == 409
    status, body, _ = app.call('/ui/studio/subscriptions/' + sub['id'], cookie=cookie)
    assert status == 200 and body['subscription']['webhook_test_pending'] == {
        'idempotency_key': 'one', 'sample_id': source['default_sample_id']}
    assert calls == [True]


def test_lost_ack_reconciles_only_exact_actual_inbox(configured, monkeypatch):
    app, cookie, _ = configured
    source, sub = create(app, cookie)
    setup = app.ui.studio_webhook_setup
    original = setup._send
    calls = []
    def lost(*args):
        calls.append(True)
        original(*args)
        raise OSError('lost response')
    monkeypatch.setattr(setup, '_send', lost)
    status, body, _ = send(app, cookie, source, sub)
    assert status == 202, body
    assert send(app, cookie, source, sub)[0] == 202 and calls == [True]


def test_current_epoch_and_tls_identity_control_availability(configured):
    app, cookie, _ = configured
    source, sub = create(app, cookie)
    authority = app.ui.studio_authority
    context = authority._context()
    studio_epoch.invalidate(authority.root, [context['pool']], 'security-reset')
    status, body, _ = app.call('/ui/studio/sources', cookie=cookie)
    actual = next(row for row in body['sources'] if row['id'] == source['id'])
    assert status == 200 and actual['available'] is False
    assert send(app, cookie, source, sub)[0] == 403


def test_registration_no_tls_never_persists_source(request):
    app, cookie, _ = request.getfixturevalue('served')
    app.call('/ui/studio/sources', cookie=cookie)
    status, body, _ = app.call('/ui/studio/sources', method='POST', cookie=cookie,
        body={'kind': 'webhook', 'provider': 'local-synthetic', 'label': 'secure listener unavailable', 'idempotency_key': 'source'})
    assert status == 403, body
    assert app.ui.studio_webhook_setup.providers()[0]['available'] is False


def test_provider_rehydrates_without_stored_owner_session(configured):
    app, cookie, session = configured
    source, _ = create(app, cookie)
    from poolhouse.fleet.studio_webhook_setup import LocalSender
    app.ui.sessions.close(session.sid)
    app.ui._studio_origins.clear()
    setup = LocalSender(app.ui, app.ui.studio_streams)
    assert setup.current(source['id'], app.ui.studio_streams.store.get('source', source['id'],
        app.ui.studio_authority._context()['scope'])['generation'])
    credentials = []
    row = setup.service.store.get('source', source['id'], setup.service.authority._context()['scope'])
    setup.holder.provision_registered(row['id'], row['generation'], lambda value: credentials.append(copy.deepcopy(value)) is None, scope=row['scope'])
    assert len(credentials) == 1 and credentials[0]['source'] == row['id']


def test_known_rejection_never_retries_same_request(configured, monkeypatch):
    app, cookie, _ = configured
    source, sub = create(app, cookie)
    calls = []
    def capacity(*args):
        calls.append(True)
        return 429, {'error': 'No capacity'}
    monkeypatch.setattr(app.ui.studio_webhook_setup, '_send', capacity)
    assert send(app, cookie, source, sub)[0] == 429
    assert send(app, cookie, source, sub)[0] == 429
    assert calls == [True]
    _, body, _ = app.call('/ui/studio/subscriptions/' + sub['id'], cookie=cookie)
    assert body['subscription']['webhook_test_pending'] is None


def test_mismatched_ack_is_interrupted_until_explicit_exact_reconciliation(configured, monkeypatch):
    app, cookie, _ = configured
    source, sub = create(app, cookie)
    setup = app.ui.studio_webhook_setup
    original = setup._send
    calls = []
    def mismatch(*args):
        calls.append(True)
        original(*args)
        return 200, {'id': '0' * 64, 'status': 'queued'}
    monkeypatch.setattr(setup, '_send', mismatch)
    assert send(app, cookie, source, sub)[0] == 409
    assert send(app, cookie, source, sub)[0] == 202
    assert calls == [True]


def test_weak_origin_cannot_configure_or_send(configured):
    app, cookie, _ = configured
    source, sub = create(app, cookie)
    weak = app.ui.sessions.open('weak', 'token')
    weak_cookie = 'poolhouse_ui=' + weak.sid
    assert send(app, weak_cookie, source, sub)[0] == 403
    status, _, _ = app.call('/ui/studio/sources', method='POST', cookie=weak_cookie,
        body={'kind': 'webhook', 'provider': 'local-synthetic', 'label': 'Weak', 'idempotency_key': 'weak'})
    assert status == 403


def test_concurrent_duplicate_test_posts_only_once(configured, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    app, cookie, _ = configured
    source, sub = create(app, cookie)
    setup = app.ui.studio_webhook_setup
    original = setup._send
    entered, release = threading.Event(), threading.Event()
    calls = []
    def controlled(*args):
        calls.append(True)
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(setup, '_send', controlled)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(send, app, cookie, source, sub)
        try:
            assert entered.wait(3)
            assert send(app, cookie, source, sub)[0] == 409
        finally:
            release.set()
        assert future.result()[0] == 202
    assert calls == [True]


def test_new_current_owner_reconciles_existing_intent_without_adopting_old_session(configured, monkeypatch):
    app, cookie, session = configured
    source, sub = create(app, cookie)
    setup = app.ui.studio_webhook_setup
    original = setup._send
    calls = []
    def wrong_receipt(*args):
        calls.append(True)
        original(*args)
        return 200, {'id': '0' * 64, 'status': 'queued'}
    monkeypatch.setattr(setup, '_send', wrong_receipt)
    assert send(app, cookie, source, sub)[0] == 409
    app.ui.sessions.close(session.sid)
    owner = app.ui.sessions.open('new-owner-session', 'launch-ticket')
    renewed_cookie = 'poolhouse_ui=' + owner.sid
    weak = app.ui.sessions.open('weak-session', 'token')
    assert send(app, 'poolhouse_ui=' + weak.sid, source, sub)[0] == 403
    assert send(app, renewed_cookie, source, sub, key='different')[0] == 409
    assert send(app, renewed_cookie, source, sub, sample=source['samples'][1]['id'])[0] == 409
    status, body, _ = send(app, renewed_cookie, source, sub)
    assert status == 202, body
    assert calls == [True]

"""Real authenticated source-to-live journeys with isolated TLS and finite record adapters."""
import time

import pytest
from test_fleet_ui import Serving

from poolhouse.fleet import studio_epoch, tls
from poolhouse.fleet.discovery import mint_pool
from poolhouse.fleet.membership import Roster


@pytest.fixture
def served(tmp_path):
    app = Serving(tmp_path)
    app.ui.root = tmp_path / 'traind'
    app.ui.settings.setup_done = True
    identity = tls.identity(tmp_path / 'tls', 'studio')
    app.ui.studio_identity = lambda: identity
    member = mint_pool('stream-pool', app.keyfile)
    Roster(member.key).enrol(identity.beacon, 'stream-device', 'test')
    session = app.ui.sessions.open('owner-session', 'launch-ticket')
    cookie = 'poolhouse_ui=' + session.sid
    try:
        yield app, cookie, session
    finally:
        engine = getattr(app.ui, 'studio', None)
        if engine:
            engine.close()
            engine.store.close()
        app.close()


def create(app, cookie):
    status, body, _ = app.call('/ui/studio/sources', cookie=cookie)
    assert status == 200, body
    source = body['sources'][0]
    status, body, _ = app.call('/ui/studio/workflows', method='POST', cookie=cookie,
                               body={'title': 'Live records', 'graph': {'nodes': [{'id': 'record', 'type': 'stream.record'}], 'edges': []}})
    assert status == 201, body
    workflow = body['workflow']
    status, body, _ = app.call('/ui/studio/subscriptions', method='POST', cookie=cookie,
                               body={'workflow_id': workflow['id'], 'revision': workflow['revision'],
                                     'source_id': source['id'], 'input_node': 'record', 'output_nodes': ['record'],
                                     'destinations': [{'kind': 'live'}], 'idempotency_key': 'create'})
    assert status == 201, body
    return body['subscription']


def test_start_sample_tail_and_browser_expiry_do_not_revoke_service(served):
    app, cookie, session = served
    sub = create(app, cookie)
    status, body, _ = app.call('/ui/studio/subscriptions/' + sub['id'] + '/start', method='POST', cookie=cookie,
                               body={'revision': sub['revision'], 'idempotency_key': 'start'})
    assert status == 200, body
    path = '/ui/studio/subscriptions/' + sub['id'] + '/events'
    status, body, _ = app.call(path, method='POST', cookie=cookie, body={'sample_id': 'sample', 'idempotency_key': 'sample-one'})
    assert status == 202, body
    event = body['event']
    again, body, _ = app.call(path, method='POST', cookie=cookie, body={'sample_id': 'sample', 'idempotency_key': 'sample-one'})
    assert again == 202 and body['event']['id'] == event['id']
    # Browser expiry removes interactive authority but leaves explicit daemon delegation.
    app.ui.sessions.close(session.sid)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        persisted = app.ui.studio_streams.store.get('stream-event', event['id'], app.ui.studio_streams.store.active()[0]['scope']) if app.ui.studio_streams.store.active() else None
        if persisted and persisted['state'] == 'done':
            break
        time.sleep(.02)
    assert persisted and persisted['state'] == 'done'
    new = app.ui.sessions.open('owner-again', 'passphrase')
    status, body, _ = app.call(path + '?tail=1&limit=1', cookie='poolhouse_ui=' + new.sid)
    assert status == 200, body
    assert body['events'][0]['id'] == event['id']
    assert body['events'][0]['result']['outputs'][0]['port'] == 'record'


def test_weak_session_cannot_delegate_and_revocation_blocks_output(served):
    app, cookie, _ = served
    sub = create(app, cookie)
    weak = app.ui.sessions.open('invited-session', 'invite')
    path = '/ui/studio/subscriptions/' + sub['id']
    status, _, _ = app.call(path + '/start', method='POST', cookie='poolhouse_ui=' + weak.sid,
                           body={'revision': sub['revision'], 'idempotency_key': 'weak'})
    assert status == 403
    status, body, _ = app.call(path + '/start', method='POST', cookie=cookie,
                               body={'revision': sub['revision'], 'idempotency_key': 'strong'})
    assert status == 200, body
    authority = app.ui.studio_authority
    context = authority._context()
    studio_epoch.invalidate(authority.root, [context['pool']], 'invitation-revoked')
    status, _, _ = app.call(path + '/events', cookie=cookie)
    assert status == 403


def test_managed_source_revoke_between_bind_and_persist_creates_nothing(served, monkeypatch):
    from poolhouse.fleet import studio_stream_routes
    app, cookie, _ = served
    create(app, cookie)
    path = app.files / 'records.jsonl'
    path.write_text('{"record":1}\n')
    original = studio_stream_routes.bind
    def bind(*args, **kwargs):
        value = original(*args, **kwargs)
        authority = app.ui.studio_authority
        studio_epoch.invalidate(authority.root, [authority._context()['pool']], 'invitation-revoked')
        return value
    monkeypatch.setattr(studio_stream_routes, 'bind', bind)
    status, body, _ = app.call('/ui/studio/sources', method='POST', cookie=cookie,
                               body={'kind': 'watched_file', 'resource_id': 'records.jsonl', 'idempotency_key': 'select'})
    assert status == 403, body
    rows = app.ui.studio_streams.store.indexed('source', app.ui.studio_authority._context()['scope'])
    assert not any(row['kind'] == 'watched_file' for row in rows)


@pytest.mark.parametrize('mode', ['group', 'sweep'])
def test_expanded_group_or_sweep_manifest_remains_exact(served, mode):
    app, cookie, _ = served
    status, body, _ = app.call('/ui/studio/sources', cookie=cookie)
    source = body['sources'][0]
    node = {'id': 'record', 'type': 'stream.record'}
    graph = {'nodes': [node], 'edges': []}
    input_node, outputs = 'record', ['record']
    if mode == 'sweep':
        node['sweep'] = {'sample': ['{}', '{"unused":1}']}
    else:
        graph = {'nodes': [{'id': 'nested', 'type': 'group', 'group': 'definition'}], 'edges': [],
                 'groups': {'definition': {'nodes': [node], 'edges': []}}}
        input_node, outputs = 'nested/record', ['nested']
    status, body, _ = app.call('/ui/studio/workflows', method='POST', cookie=cookie,
                               body={'title': 'Expanded record', 'graph': graph})
    assert status == 201, body
    workflow = body['workflow']
    status, body, _ = app.call('/ui/studio/subscriptions', method='POST', cookie=cookie,
                               body={'workflow_id': workflow['id'], 'revision': workflow['revision'],
                                     'source_id': source['id'], 'input_node': input_node, 'output_nodes': outputs,
                                     'destinations': [{'kind': 'live'}], 'idempotency_key': 'expanded'})
    assert status == 201, body
    sub = body['subscription']
    status, body, _ = app.call('/ui/studio/subscriptions/' + sub['id'] + '/start', method='POST', cookie=cookie,
                               body={'revision': 1, 'idempotency_key': 'start-expanded'})
    assert status == 200, body
    path = '/ui/studio/subscriptions/' + sub['id'] + '/events'
    status, body, _ = app.call(path, method='POST', cookie=cookie,
                               body={'sample_id': 'sample', 'idempotency_key': 'expanded-event'})
    assert status == 202, body
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        _, body, _ = app.call(path + '?tail=1', cookie=cookie)
        if body['events'][0]['state'] in {'done', 'failed', 'interrupted'}:
            break
        time.sleep(.02)
    assert body['events'][0]['state'] == 'done', body
    assert len(body['events'][0]['result']['outputs']) == (2 if mode == 'sweep' else 1)


def test_restart_resumes_delegation_without_any_browser_identity(served):
    from poolhouse.fleet.studio_service import initialize
    app, cookie, session = served
    sub = create(app, cookie)
    status, body, _ = app.call('/ui/studio/subscriptions/' + sub['id'] + '/start', method='POST', cookie=cookie,
                               body={'revision': 1, 'idempotency_key': 'start'})
    assert status == 200, body
    service = app.ui.studio_streams
    actual = service.store.get('subscription', sub['id'], app.ui.studio_authority._context()['scope'])
    event = service.ingest(actual, {'id': 'restart-event', 'generation': 'examples-v1', 'payload': {'record': 'after restart'}})
    engine = app.ui.studio
    engine.close()
    engine.store.close()
    app.ui.sessions.close(session.sid)
    del app.ui.studio
    initialize(app.ui)
    assert app.ui._studio_principals == {} and app.ui._studio_origins == {}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = app.ui.studio.store.get('stream-event', event['id'], actual['scope'])
        if row['state'] == 'done':
            break
        time.sleep(.02)
    assert row['state'] == 'done'
    assert row['principal']['kind'] == 'studio_subscription'


def test_pause_and_cancel_release_source_scanners(served, monkeypatch):
    app, cookie, _ = served
    sub = create(app, cookie)
    status, body, _ = app.call('/ui/studio/subscriptions/' + sub['id'] + '/start', method='POST', cookie=cookie,
                               body={'revision': 1, 'idempotency_key': 'start'})
    assert status == 200, body
    releases = []
    monkeypatch.setattr(app.ui.studio_streams.sources, 'release', releases.append)
    path = '/ui/studio/subscriptions/' + sub['id']
    status, body, _ = app.call(path + '/pause', method='POST', cookie=cookie,
                               body={'revision': body['subscription']['revision'], 'idempotency_key': 'pause'})
    assert status == 200 and body['subscription']['can_resume'], body
    status, body, _ = app.call(path + '/cancel', method='POST', cookie=cookie,
                               body={'revision': body['subscription']['revision'], 'idempotency_key': 'cancel'})
    assert status == 200 and body['subscription']['state'] == 'cancelled', body
    assert releases == [sub['id'], sub['id']]


def test_duplicate_cancel_finishes_exact_run_after_intent_crash(served, monkeypatch):
    app, cookie, _ = served
    sub = create(app, cookie)
    path = '/ui/studio/subscriptions/' + sub['id']
    status, body, _ = app.call(path + '/start', method='POST', cookie=cookie,
                               body={'revision': 1, 'idempotency_key': 'start'})
    assert status == 200, body
    service = app.ui.studio_streams
    service.close()
    actual = service.store.get('subscription', sub['id'], app.ui.studio_authority._context()['scope'])
    event = service.ingest(actual, {'id': 'cancel-event', 'generation': 'examples-v1', 'payload': {'value': 1}})
    # Existing run committed but service never received its submit acknowledgement.
    run = app.ui.studio.submit_event(actual['service_principal'], actual['workflow'], event['envelope'])
    attempts = []
    original = app.ui.studio.cancel
    def stop(principal, ident):
        attempts.append(ident)
        return original(principal, ident)
    monkeypatch.setattr(app.ui.studio, 'cancel', stop)
    original_revoke = service.authority.revoke
    def crash(*args):
        raise OSError('simulated shutdown after cancellation intent')
    monkeypatch.setattr(service.authority, 'revoke', crash)
    request = {'revision': actual['revision'], 'idempotency_key': 'cancel'}
    status, _, _ = app.call(path + '/cancel', method='POST', cookie=cookie, body=request)
    assert status == 400 and attempts == []
    monkeypatch.setattr(service.authority, 'revoke', original_revoke)
    status, body, _ = app.call(path + '/cancel', method='POST', cookie=cookie, body=request)
    assert status == 200, body
    assert attempts == [run['id']]
    assert service.store.get('stream-event', event['id'], actual['scope'])['run_id'] == run['id']
    with pytest.raises(PermissionError):
        service.authority.get(actual['service_principal'])


def test_actual_hmac_ingress_uses_only_source_authority_and_atomic_backpressure(served):
    import hashlib
    import hmac
    import http.client
    import ssl

    from poolhouse.fleet.studio_webhooks import _frame
    from poolhouse.studio.store import digest

    app, cookie, session = served
    existing = create(app, cookie)
    status, body, _ = app.call('/ui/studio/sources', method='POST', cookie=cookie,
                               body={'kind': 'webhook', 'label': 'Software sender', 'idempotency_key': 'webhook'})
    assert status == 201 and 'secret' not in str(body), body
    source_id = body['source']['id']
    service = app.ui.studio_streams
    scope = app.ui.studio_authority._context()['scope']
    source = service.store.get('source', source_id, scope)
    principal = {'id': digest({'session': session.sid}), 'scope': scope, 'owner': True}
    credentials = {}
    app.ui.studio_webhooks.configure_sender(principal, source_id, source['generation'],
                                           lambda value: credentials.update(value) is None)
    stored = service.store.get('subscription', existing['id'], scope)
    status, body, _ = app.call('/ui/studio/subscriptions', method='POST', cookie=cookie,
                               body={'workflow_id': stored['workflow']['id'], 'revision': 1,
                                     'source_id': source_id, 'input_node': 'record', 'output_nodes': ['record'],
                                     'destinations': [{'kind': 'live'}], 'idempotency_key': 'webhook-sub'})
    assert status == 201, body
    sub = body['subscription']
    status, body, _ = app.call('/ui/studio/subscriptions/' + sub['id'] + '/start', method='POST', cookie=cookie,
                               body={'revision': 1, 'idempotency_key': 'start-webhook'})
    assert status == 200, body
    service.close()  # Hold the admitted slot to prove bounded intake, without compute.
    context = ssl.create_default_context(cafile=str(app.ui.studio_identity().certfile))
    context.check_hostname = False
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    def send(raw, event='sender-one'):
        timestamp = str(int(time.time()))
        signature = hmac.new(credentials['secret'].encode(), _frame(source_id, source['generation'], event, timestamp, raw), hashlib.sha256).hexdigest()
        connection = http.client.HTTPSConnection('127.0.0.1', app.port, context=context)
        try:
            connection.request('POST', '/studio/ingest/' + source_id, body=raw,
                               headers={'Content-Type': 'application/json', 'X-Poolhouse-Event-ID': event,
                                        'X-Poolhouse-Timestamp': timestamp, 'X-Poolhouse-Generation': source['generation'],
                                        'X-Poolhouse-Signature': signature})
            response = connection.getresponse()
            result = response.status, response.read()
            return result
        finally:
            connection.close()
    first = send(b'{"record":1}')
    assert first[0] == 200
    assert send(b'{"record":1}') == first
    assert send(b'{ "record":1}') [0] == 409
    assert send(b'{"record":2}', event='sender-two')[0] == 429
    current = service.store.get('subscription', sub['id'], scope)
    assert current['next_sequence'] == 2


def test_actual_router_tail_after_and_preview_queries_are_not_discarded(served):
    app, cookie, _ = served
    sub = create(app, cookie)
    path = '/ui/studio/subscriptions/' + sub['id']
    status, body, _ = app.call(path + '/start', method='POST', cookie=cookie,
                               body={'revision': 1, 'idempotency_key': 'start'})
    assert status == 200, body
    service = app.ui.studio_streams
    service.close()
    scope = app.ui.studio_authority._context()['scope']
    for number in range(60):
        current = service.store.get('subscription', sub['id'], scope)
        service.ingest(current, {'id': 'history-' + str(number), 'generation': 'examples-v1', 'error': 'invalid record'})
    status, body, _ = app.call(path + '/events?tail=1&limit=2', cookie=cookie)
    assert status == 200, body
    assert [event['sequence'] for event in body['events']] == [59, 60]
    assert body['next_cursor'] == 60
    status, body, _ = app.call(path + '/events?after=55&limit=2', cookie=cookie)
    assert status == 200, body
    assert [event['sequence'] for event in body['events']] == [56, 57]
    assert body['next_cursor'] == 57
    status, _, _ = app.call(path + '/preview?sample_id=unregistered', cookie=cookie)
    assert status == 404


@pytest.mark.parametrize('mode', ['file', 'folder'])
def test_original_source_labels_expose_only_selected_basename(served, mode):
    app, cookie, _ = served
    create(app, cookie)
    selected = app.files / ('incoming.jsonl' if mode == 'file' else 'incoming-folder')
    if mode == 'file':
        selected.write_text('{"record":1}\n')
    else:
        selected.mkdir()
    status, body, _ = app.call('/ui/studio/sources', method='POST', cookie=cookie,
                               body={'kind': 'watched_file', 'resource_id': selected.name, 'idempotency_key': 'select'})
    assert status == 201, body
    assert body['source']['label'] == selected.name
    assert 'binding' not in body['source'] and str(selected) not in str(body)

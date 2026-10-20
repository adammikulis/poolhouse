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

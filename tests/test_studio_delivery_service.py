"""Actual owner/source/run/outbox routes with isolated data and no external requests."""
import copy

import pytest
import test_studio_stream_http
from test_studio_stream_http import create

served = test_studio_stream_http.served


def prepared(app, cookie, *, external_only=False):
    original = create(app, cookie)
    service = app.ui.studio_streams
    service._stop.set()
    service._thread.join()
    service.outbox.stop.set()
    service.outbox.thread.join()
    status, body, _ = app.call('/ui/studio/destinations', method='POST', cookie=cookie,
                               body={'label': 'Receiver', 'url': 'https://receiver.invalid/events', 'idempotency_key': 'receiver'})
    assert status == 201, body
    destination = body['destination']
    outputs = [{'node': 'record', 'port': 'record'}]
    if external_only:
        original, outputs = add_external_branch(app, cookie, original)
    config = {'workflow_id': original['workflow_id'], 'revision': original['workflow_revision'],
              'source_id': original['source_id'], 'input_node': original['input_node'], 'output_nodes': original['output_nodes'],
              'destinations': [{'kind': 'live'}, {'kind': 'webhook', 'id': destination['id'], 'revision': destination['revision'], 'outputs': outputs}], 'idempotency_key': 'outbound'}
    status, body, _ = app.call('/ui/studio/subscriptions', method='POST', cookie=cookie, body=config)
    assert status == 201, body
    sub = body['subscription']
    path = '/ui/studio/subscriptions/' + sub['id']
    status, body, _ = app.call(path + '/start', method='POST', cookie=cookie,
                               body={'revision': sub['revision'], 'idempotency_key': 'start'})
    assert status == 200, body
    status, body, _ = app.call(path + '/events', method='POST', cookie=cookie,
                               body={'sample_id': 'sample', 'idempotency_key': 'record-one'})
    assert status == 202, body
    event = body['event']
    row = service.store.indexed('subscription', app.ui.studio_authority._context()['scope'])
    sub = next(item for item in row if item['id'] == sub['id'])
    service.advance(sub)
    persisted = service.store.get('stream-event', event['id'], sub['scope'])
    with service.engine.lock:
        service.engine.advance(service.store.get('run', persisted['run_id'], sub['scope']))
    service.advance(sub)
    event = service.store.get('stream-event', event['id'], sub['scope'])
    assert event['state'] == 'running' and event['deliveries']
    return service, sub, event, destination, path


def test_actual_http_outbound_receipt_settles_and_retry_never_resends(served):
    app, cookie, _ = served
    service, sub, event, _, path = prepared(app, cookie)
    calls = []
    def send(destination, envelope, *, authorize):
        assert authorize() is True
        calls.append(copy.deepcopy(envelope))
        return {'state': 'done'}
    service.outbox.send = send
    row = service.outbox.attempt(event['deliveries'][0], sub['scope'])
    assert row['state'] == 'done'
    assert service.outbox.attempt(row['id'], sub['scope'])['state'] == 'done'
    service.advance(sub)
    status, body, _ = app.call(path + '/events?tail=1', cookie=cookie)
    assert status == 200, body
    assert body['events'][0]['state'] == 'done' and len(calls) == 1
    assert calls[0]['body']['event_id'] == event['id']
    assert calls[0]['body']['outputs'][0]['port'] == 'record'
    assert service.store.get('subscription', sub['id'], sub['scope'])['inflight'] is None


def test_current_destination_change_blocks_send_and_record_body_mutation_refuses(served):
    app, cookie, _ = served
    service, sub, event, destination, _ = prepared(app, cookie)
    ident = event['deliveries'][0]
    row = service.store.get('delivery', ident, sub['scope'])
    original = copy.deepcopy(row)
    row['envelope']['body']['outputs'][0]['value'] = {'forged': True}
    service.engine.store.put('delivery', row)
    with pytest.raises(PermissionError, match='content changed'):
        service.delivery_current(row)
    service.engine.store.put('delivery', original)
    status, body, _ = app.call('/ui/studio/destinations/' + destination['id'], method='PUT', cookie=cookie,
                               body={'label': 'New receiver', 'url': 'https://other.invalid/events', 'revision': 1, 'idempotency_key': 'change'})
    assert status == 200, body
    calls = []
    service.outbox.send = lambda *args, **kw: calls.append(args) or {'state': 'done'}
    assert service.outbox.attempt(ident, sub['scope'])['state'] == 'blocked'
    assert calls == []
    status, body, _ = app.call('/ui/studio/deliveries/' + ident, cookie=cookie)
    assert status == 200 and body['delivery']['not_sent'], body


def test_revoked_service_history_owner_attestation_settles_without_sending(served):
    app, cookie, _ = served
    service, sub, event, _, path = prepared(app, cookie)
    calls = []
    service.outbox.send = lambda *args, **kwargs: calls.append(args) or {'state': 'interrupted'}
    row = service.outbox.attempt(event['deliveries'][0], sub['scope'])
    assert row['state'] == 'interrupted'
    service.advance(sub)
    status, body, _ = app.call(path + '/cancel', method='POST', cookie=cookie,
                               body={'revision': sub['revision'], 'idempotency_key': 'cancel'})
    assert status == 200 and body['subscription']['state'] == 'cancelled', body
    status, body, _ = app.call(path + '/deliveries', cookie=cookie)
    assert status == 200 and body['deliveries'][0]['id'] == row['id'], body
    assert 'body' not in body['deliveries'][0] and 'principal' not in body['deliveries'][0]
    weak = app.ui.sessions.open('weak-history', 'invite')
    assert app.call(path + '/deliveries', cookie='poolhouse_ui=' + weak.sid)[0] == 403
    app.ui.studio_authorize = lambda *args: False
    assert app.call(path + '/deliveries', cookie=cookie)[0] == 403
    app.ui.studio_authorize = lambda *args: True
    request = {'delivery_id': row['id'], 'request_digest': row['envelope']['request_digest'],
               'destination_revision': row['destination']['revision'], 'idempotency_key': 'attest',
               'evidence': 'owner_attestation', 'outcome': 'delivered'}
    status, body, _ = app.call('/ui/studio/deliveries/' + row['id'] + '/reconcile', method='POST', cookie=cookie, body=request)
    assert status == 200 and body['delivery']['reconciliation']['weaker_than_receiver_receipt'], body
    current = service.store.get('subscription', sub['id'], sub['scope'])
    assert current['state'] == 'cancelled' and current['inflight'] is None and len(calls) == 1
    assert app.call('/ui/studio/deliveries/' + row['id'] + '/reconcile', method='POST', cookie=cookie, body=request)[0] == 200
    assert len(calls) == 1


def add_external_branch(app, cookie, original):
    status, body, _ = app.call('/ui/studio/workflows/' + original['workflow_id'], cookie=cookie)
    assert status == 200, body
    workflow = body['workflow']
    workflow['graph']['nodes'].append({'id': 'external', 'type': 'prompt', 'parameters': {'template': 'only the receiver'}})
    status, body, _ = app.call('/ui/studio/workflows/' + workflow['id'], method='PUT', cookie=cookie,
                               body={'title': workflow['title'], 'graph': workflow['graph'], 'revision': workflow['revision']})
    assert status == 200, body
    original['workflow_revision'] = body['workflow']['revision']
    return original, [{'node': 'external', 'port': 'prompt'}]


def test_webhook_only_branch_executes_without_widening_live_projection(served):
    app, cookie, _ = served
    service, sub, event, _, path = prepared(app, cookie, external_only=True)
    calls = []
    service.outbox.send = lambda destination, envelope, **kw: calls.append(envelope) or {'state': 'done'}
    row = service.outbox.attempt(event['deliveries'][0], sub['scope'])
    assert row['state'] == 'done'
    service.advance(sub)
    status, body, _ = app.call(path + '/events?tail=1', cookie=cookie)
    assert status == 200, body
    assert [output['node'] for output in body['events'][0]['result']['outputs']] == ['record']
    assert calls[0]['body']['outputs'] == [{'node': 'external', 'port': 'prompt', 'value': 'only the receiver'}]
    run = service.store.get('run', event['run_id'], sub['scope'])
    assert run['nodes']['external']['state'] == 'done'
    assert event['envelope']['output_nodes'] == ['record', 'external']
    assert event['live_output_nodes'] == ['record']


def test_duplicate_destination_different_projection_rejects_before_any_run(served):
    app, cookie, _ = served
    original = create(app, cookie)
    status, body, _ = app.call('/ui/studio/destinations', method='POST', cookie=cookie,
                               body={'label': 'One receiver', 'url': 'https://receiver.invalid/events', 'idempotency_key': 'receiver'})
    assert status == 201, body
    ident = body['destination']['id']
    config = {'workflow_id': original['workflow_id'], 'revision': original['workflow_revision'],
              'source_id': original['source_id'], 'input_node': 'record', 'output_nodes': ['record'],
              'destinations': [{'kind': 'webhook', 'id': ident, 'revision': body['destination']['revision'], 'outputs': [{'node': 'record', 'port': 'record'}]},
                               {'kind': 'webhook', 'id': ident, 'revision': body['destination']['revision'], 'outputs': [{'node': 'record', 'port': 'other'}]}],
              'idempotency_key': 'duplicate'}
    status, body, _ = app.call('/ui/studio/subscriptions', method='POST', cookie=cookie, body=config)
    assert status == 400 and 'each destination only once' in body['error'], body
    scope = app.ui.studio_authority._context()['scope']
    assert app.ui.studio.store.records('run', scope) == []


@pytest.mark.parametrize('race', ['before', 'during', 'truthy'])
def test_stale_selected_revision_never_creates_a_subscription(served, monkeypatch, race):
    app, cookie, _ = served
    original = create(app, cookie)
    status, body, _ = app.call('/ui/studio/destinations', method='POST', cookie=cookie,
                               body={'label': 'Receiver', 'url': 'https://receiver.invalid/events', 'idempotency_key': 'receiver'})
    assert status == 201, body
    destination = body['destination']
    change = {'label': 'Changed', 'url': 'https://other.invalid/events', 'revision': 1, 'idempotency_key': 'change'}
    if race == 'during':
        registry = app.ui.studio_streams.destination_registry
        snapshot = registry.snapshot
        changed = []
        def raced(principal, ident):
            value = snapshot(principal, ident)
            if not changed:
                registry.mutate(principal, 'update', change, ident)
                changed.append(True)
            return value
        monkeypatch.setattr(registry, 'snapshot', raced)
    elif race == 'truthy':
        app.ui.studio_streams.destinations_current = lambda *args: 'yes'
    else:
        status, body, _ = app.call('/ui/studio/destinations/' + destination['id'], method='PUT', cookie=cookie, body=change)
        assert status == 200, body
    config = {'workflow_id': original['workflow_id'], 'revision': original['workflow_revision'],
              'source_id': original['source_id'], 'input_node': 'record', 'output_nodes': ['record'],
              'destinations': [{'kind': 'webhook', 'id': destination['id'], 'revision': 1,
                                'outputs': [{'node': 'record', 'port': 'record'}]}], 'idempotency_key': 'stale'}
    status, body, _ = app.call('/ui/studio/subscriptions', method='POST', cookie=cookie, body=config)
    assert status == 409, body
    service = app.ui.studio_streams
    scope = app.ui.studio_authority._context()['scope']
    assert len(service.store.indexed('subscription', scope)) == 1
    assert service.engine.store.records('run', scope) == []

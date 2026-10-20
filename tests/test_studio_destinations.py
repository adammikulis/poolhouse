"""Registered endpoint revisions and explicit owner actions, with no external network."""
from __future__ import annotations

import io
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from poolhouse import httpguard
from poolhouse.fleet.studio_authority import StudioAuthority
from poolhouse.fleet.studio_destinations import create, route
from poolhouse.studio.outbox import Outbox
from poolhouse.studio.store import Conflict, StudioStore
from poolhouse.studio.stream_store import StreamStore


@pytest.fixture
def registry(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('Destination configuration must not contact a network endpoint')
    monkeypatch.setattr(socket, 'getaddrinfo', no_network)
    monkeypatch.setattr(httpguard, 'fetch', no_network)
    state = {'origin': 'launch-ticket', 'active': True, 'history': True, 'grant': True, 'sends': []}
    principal = {'id': 'a' * 64, 'scope': 'current-scope', 'owner': True}
    authority = StudioAuthority(tmp_path / 'authority', current=lambda: {
        'pool': '1' * 64, 'device': '2' * 64, 'scope': principal['scope'], 'active': state['active']},
        owner=lambda caller: state['origin'] if caller['id'] == principal['id'] else '',
        resources=lambda *args: True)
    store = StudioStore(tmp_path / 'studio.ladybug')
    service = SimpleNamespace(engine=SimpleNamespace(store=store), store=StreamStore(store), authority=authority)
    destinations = create(SimpleNamespace(), service)
    def delivery_owner(owner, row):
        authority._owner(owner)
        return state['history'] and row['scope'] == owner['scope']
    def current(row):
        return state['grant'] and destinations.current(row['principal'], row['destination'])
    def send(destination, envelope, *, authorize):
        assert authorize() is True
        state['sends'].append(envelope['delivery_id'])
        return {'state': 'interrupted', 'reason': 'Unconfirmed receiver receipt'}
    service.delivery_owner, service.delivery_current = delivery_owner, current
    service.outbox = Outbox(store, authorize=current, send=send)
    try:
        yield destinations, service, principal, state
    finally:
        service.outbox.close()
        authority.close()
        store.close()


def configuration(key='create', **changes):
    return {'label': 'Decision receiver', 'url': 'https://receiver.example/decisions', 'idempotency_key': key, **changes}


class Request:
    def __init__(self, path, method='GET', body=None, raw=None):
        self.path, self.method = path, method
        data = raw if raw is not None else json.dumps(body or {}).encode()
        self.handler = SimpleNamespace(rfile=io.BytesIO(data))
        self.length, self.response = str(len(data)), None
    def header(self, name, default=''):
        return self.length if name == 'Content-Length' else default
    def send(self, status, body):
        self.response = status, body


def request(service, principal, path, method='GET', body=None):
    handler = Request('/ui/studio/' + path, method, body)
    assert route(handler, service, principal) is True
    return handler.response


def delivery(registry):
    destinations, service, principal, _state = registry
    row = destinations.mutate(principal, 'create', configuration())
    snapshot = destinations.snapshot(principal, row['id'])
    event = {'id': 'e' * 64, 'subscription': 's' * 32, 'scope': principal['scope'],
             'principal': {'id': 'studio-grant:test', 'scope': principal['scope'], 'kind': 'studio_subscription'}}
    row = service.outbox.stage(event, snapshot, {'private_record': 'local-only-content'})
    return service.outbox.attempt(row['id'], row['scope'])


def action(row, key='action', **extra):
    return {'delivery_id': row['id'], 'request_digest': row['envelope']['request_digest'],
            'destination_revision': row['destination']['revision'], 'idempotency_key': key, **extra}


def test_crud_is_revision_checked_idempotent_and_never_contacts_endpoint(registry):
    destinations, service, principal, _ = registry
    status, response = request(service, principal, 'destinations', 'POST', configuration())
    assert status == 201
    original = response['destination']
    assert set(original) == {'id', 'revision', 'url', 'label', 'enabled'}
    assert request(service, principal, 'destinations', 'POST', configuration())[1] == response
    snapshot = destinations.snapshot(principal, original['id'])
    assert set(snapshot) == {'id', 'revision', 'url'} and destinations.current(principal, snapshot)
    with pytest.raises(Conflict):
        destinations.mutate(principal, 'create', configuration(url='https://other.example/events'))
    update = configuration('update', revision=1, url='https://other.example/events')
    changed = request(service, principal, 'destinations/' + original['id'], 'PUT', update)[1]['destination']
    assert changed['revision'] == 2 and not destinations.current(principal, snapshot)
    assert request(service, principal, 'destinations/' + original['id'], 'PUT', update)[1]['destination'] == changed
    assert destinations.mutate(principal, 'create', configuration()) == original
    with pytest.raises(Conflict):
        destinations.mutate(principal, 'update', configuration('stale', revision=1), original['id'])
    same = destinations.mutate(principal, 'update', {**update, 'revision': 2, 'idempotency_key': 'unchanged'}, original['id'])
    assert same['revision'] == 2
    revoked = request(service, principal, 'destinations/' + original['id'], 'DELETE', {'revision': 2, 'idempotency_key': 'revoke'})[1]['destination']
    assert revoked['revision'] == 3 and revoked['enabled'] is False
    with pytest.raises(PermissionError):
        destinations.snapshot(principal, original['id'])
    with pytest.raises(PermissionError):
        destinations.mutate(principal, 'update', configuration('reactivate', revision=3), original['id'])
    assert request(service, principal, 'destinations')[1]['destinations'] == [revoked]
    audits = [destinations.graph.get_doc(key) for key in destinations.graph.doc_keys() if key.endswith(':audit')]
    assert audits and all('https://' not in json.dumps(row) for row in audits)


@pytest.mark.parametrize('url', ['http://receiver.example/', 'https://user:secret@receiver.example/',
    'https://127.0.0.1/', 'https://[::1]/', 'https://receiver.example/#fragment',
    'https://receiver.example/\nsecret', 'https://localhost/', 'https://[fe80::1%25zone]/'])
def test_unsafe_endpoint_rejected_without_echoing_input(registry, url):
    destinations, _, principal, _ = registry
    with pytest.raises(ValueError) as error:
        destinations.mutate(principal, 'create', configuration(url=url))
    assert url not in str(error.value)
    assert destinations.list(principal) == []


def test_actual_origin_scope_and_membership_not_owner_flag_authorize(registry):
    destinations, _, principal, state = registry
    state['origin'] = 'invite'
    with pytest.raises(PermissionError):
        destinations.mutate(principal, 'create', configuration())
    state['origin'] = 'passphrase'
    row = destinations.mutate(principal, 'create', configuration())
    with pytest.raises(PermissionError):
        destinations.get({**principal, 'scope': 'copied-pool'}, row['id'])
    state['active'] = False
    with pytest.raises(PermissionError):
        destinations.list(principal)
    state['active'], state['origin'] = True, ''
    with pytest.raises(PermissionError):
        destinations.mutate(principal, 'create', configuration())


def test_concurrent_revision_changes_admit_only_one_config(registry):
    destinations, _, principal, _ = registry
    row = destinations.mutate(principal, 'create', configuration())
    def update(index):
        try:
            return destinations.mutate(principal, 'update', configuration(str(index), revision=1,
                url=f'https://receiver.example/{index}'), row['id'])['revision']
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, (1, 2)), key=str) == [2, 'conflict']


def test_route_rejects_unknown_fields_duplicate_json_and_boolean_revision(registry):
    destinations, service, principal, _ = registry
    with pytest.raises(ValueError):
        request(service, principal, 'destinations', 'POST', configuration(headers={'Authorization': 'not-accepted'}))
    handler = Request('/ui/studio/destinations', 'POST', raw=b'{"url":"https://receiver.example/","url":"https://other.example/"}')
    with pytest.raises(ValueError):
        route(handler, service, principal)
    row = destinations.mutate(principal, 'create', configuration())
    with pytest.raises(ValueError):
        request(service, principal, 'destinations/' + row['id'], 'DELETE', {'revision': True, 'idempotency_key': 'revoke'})


def test_historical_attestation_never_restores_execution_or_chooses_new_endpoint(registry):
    _, service, principal, state = registry
    row = delivery(registry)
    assert row['state'] == 'interrupted' and len(state['sends']) == 1
    state['grant'] = False
    status, view = request(service, principal, 'deliveries/' + row['id'])
    assert status == 200 and 'local-only-content' not in json.dumps(view)
    assert 'principal' not in view['delivery'] and 'envelope' not in view['delivery']
    request_body = action(row, outcome='not_delivered', evidence='owner_attestation')
    _, response = request(service, principal, 'deliveries/' + row['id'] + '/reconcile', 'POST', request_body)
    assert response['delivery']['state'] == 'blocked'
    assert response['delivery']['reconciliation']['weaker_than_receiver_receipt'] is True
    assert request(service, principal, 'deliveries/' + row['id'] + '/reconcile', 'POST', request_body)[1] == response
    with pytest.raises(PermissionError):
        request(service, principal, 'deliveries/' + row['id'] + '/retry', 'POST', action(row))
    state['grant'] = True
    with pytest.raises(ValueError):
        request(service, principal, 'deliveries/' + row['id'] + '/retry', 'POST', action(row, url='https://other.example/'))
    status, retried = request(service, principal, 'deliveries/' + row['id'] + '/retry', 'POST', action(row))
    assert status == 202 and retried['delivery']['state'] == 'queued' and len(state['sends']) == 1
    assert request(service, principal, 'deliveries/' + row['id'] + '/retry', 'POST', action(row))[1] == retried
    assert len(state['sends']) == 1  # The route queues an exact intent; it never sends synchronously.


def test_resource_or_interactive_revocation_blocks_delivery_reads_and_actions(registry):
    _, service, principal, state = registry
    row = delivery(registry)
    state['history'] = False
    for suffix, method, body in [('', 'GET', None), ('/reconcile', 'POST', action(row, outcome='delivered', evidence='owner_attestation'))]:
        with pytest.raises(PermissionError):
            request(service, principal, 'deliveries/' + row['id'] + suffix, method, body)
    state['history'], state['origin'] = True, ''
    with pytest.raises(PermissionError):
        request(service, principal, 'deliveries/' + row['id'])
    state['origin'] = 'launch-ticket'
    with pytest.raises(PermissionError):
        request(service, {**principal, 'scope': 'unrelated-scope'}, 'deliveries/' + row['id'])

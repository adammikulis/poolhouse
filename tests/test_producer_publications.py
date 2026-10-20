"""Receiver composition uses real durable log/admission and one controlled transport attempt."""
import json
from types import SimpleNamespace

import pytest

from poolhouse.fleet.producer_bridge import signed_request
from poolhouse.fleet.producer_connections import Connections
from poolhouse.fleet.producer_publications import Publications
from poolhouse.studio.store import Conflict, StudioStore, digest
from poolhouse.studio.stream_store import StreamStore


@pytest.fixture
def setup(tmp_path):
    base = StudioStore(tmp_path / 'studio.db')
    store = StreamStore(base)
    owner = {'id': 'current-owner', 'scope': 'receiver-scope'}
    source = store.create('source', owner, {'kind': 'webhook', 'provider': 'software-producer',
                                          'generation': 'a' * 32}, 'source')
    principal = {'id': 'service-grant', 'scope': owner['scope'], 'grant_id': 'b' * 32}
    sub = store.create('subscription', owner, {'source_id': source['id'], 'input_node': 'record',
        'output_nodes': ['record'], 'execution_output_nodes': ['record']}, 'subscription')
    sub.update(state='active', grant=principal['grant_id'], service_principal=principal)
    store.put_subscription(sub)
    store.graph.put_doc('studio.webhook-subscription:' + source['id'], {'subscription': sub['id'],
        'generation': source['generation'], 'scope': sub['scope'], 'grant': sub['grant']})
    binding = {'source': source['id'], 'generation': source['generation'], 'scope': source['scope'],
        'epoch': 'c' * 64, 'pool': 'd' * 64, 'receiver': 'receiver-certificate', 'producer': 'producer-certificate',
        'instance': 'e' * 64, 'runtime': 'f' * 64, 'implementation': '1' * 64,
        'sdk': {'pid': 51, 'started': 1700000000.0}, 'bridge': {'pid': 52, 'started': 1700000001.0},
        'listener': {'path': '/private/source.sock', 'dev': 1, 'ino': 2}, 'activation': '2' * 64}
    store.graph.put_doc(Connections._key(source['id']), {'binding': binding, 'phase': 'ready'})
    state = {'current': True, 'mode': 'normal', 'calls': 0, 'bytes': []}
    connections = SimpleNamespace(configured=lambda ident, generation: state['current']
        and store.get('source', ident, sub['scope'])['generation'] == generation)
    service = SimpleNamespace(store=store, authorized=lambda actual, caller: state['current']
        and actual.get('grant') == caller.get('grant_id'))
    service.ingest = lambda actual, event: store.admit(actual, event, actual['service_principal'], service.authorized)

    class Holder:
        def provision_registered(self, ident, generation, deliver, *, scope):
            assert ident == source['id'] and generation == source['generation'] and scope == source['scope']
            assert deliver({'source': ident, 'generation': generation, 'secret': 'synthetic-holder-value'}) is True

    class Sender:
        def _send(self, intent, credential, authorize):
            assert not store.lock._is_owned(), 'network under durable store lock'
            authorize()
            state['calls'] += 1
            state['bytes'].append(intent['raw'])
            if state['mode'] == 'before':
                raise OSError('no receipt')
            if state['mode'] == 'unproved429':
                return 429, {}
            if state['mode'] == 'revoke':
                state['current'] = False
            event = publications.admission.admit(sub, {'id': intent['event_id'], 'generation': intent['generation'],
                'payload': json.loads(intent['raw']), 'digest': intent['wire_digest'], 'attempt': digest(intent['attempt'])})
            if event == {'rejected': True}:
                return 429, {}
            if state['mode'] == 'lost':
                raise OSError('lost receipt after actual inbox commit')
            if state['mode'] == 'mismatch':
                return 200, {'id': '0' * 64, 'status': 'queued'}
            if state['mode'] == 'invalidstatus':
                return 200, {'id': event['id'], 'status': 'invented'}
            if state['mode'] == 'extrafield':
                return 200, {'id': event['id'], 'status': 'queued', 'owner': True}
            if state['mode'] in {'queued', 'running'}:
                store.store.put('stream-event', {**event, 'state': 'done'})
                return 200, {'id': event['id'], 'status': state['mode']}
            return 200, {'id': event['id'], 'status': event['state']}

    publications = Publications(SimpleNamespace(studio_webhooks=Holder()), service, connections, Sender())
    yield SimpleNamespace(publications=publications, store=store, binding=binding, state=state, sub=sub,
                          caller={'certificate': binding['producer'], 'pool': binding['pool']})
    base.close()


def receive(setup, action='publish', event='order-1', payload=b'{"amount":17}'):
    request = signed_request(b'x' * 32, setup.binding, 'bridge-' + action, event,
                             payload if action != 'receipt' else None)
    request.pop('mac')
    return setup.publications.receive(setup.caller, 'producer-' + action, request)


def test_actual_admission_and_immutable_duplicate_send_only_once(setup):
    first = receive(setup)
    assert first['state'] == 'done' and first['receipt']['status'] == 'queued'
    assert receive(setup) == first
    assert setup.state['calls'] == 1
    with pytest.raises(Conflict):
        receive(setup, payload=b'{"amount": 17}')
    assert setup.state['calls'] == 1


@pytest.mark.parametrize('mode', ['lost', 'mismatch', 'invalidstatus', 'extrafield'])
def test_uncertain_ack_reconciles_exact_original_inbox_without_resending(setup, mode):
    setup.state['mode'] = mode
    assert receive(setup)['state'] == 'interrupted'
    assert receive(setup, 'receipt')['state'] == 'done'
    assert receive(setup)['state'] == 'done'
    assert setup.state['calls'] == 1


@pytest.mark.parametrize('mode', ['queued', 'running'])
def test_receipt_state_may_advance_while_the_same_exact_ack_is_read(setup, mode):
    setup.state['mode'] = mode
    result = receive(setup)
    assert result['state'] == 'done' and result['receipt']['status'] == 'done'
    assert setup.state['calls'] == 1


@pytest.mark.parametrize('mode', ['before', 'unproved429'])
def test_transport_failure_or_plain429_is_unknown_and_blocks_other_ids(setup, mode):
    setup.state['mode'] = mode
    assert receive(setup)['state'] == 'interrupted'
    assert receive(setup, 'receipt')['state'] == 'interrupted'
    with pytest.raises(Conflict):
        receive(setup, event='order-2')
    with pytest.raises(Conflict):
        receive(setup, 'retry', payload='3' * 32)
    assert setup.state['calls'] == 1


def test_only_definitive_marker_permits_explicit_attempt_bound_retry(setup):
    busy = {**setup.sub, 'inflight': 'older-event'}
    setup.store.put_subscription(busy)
    assert receive(setup)['state'] == 'rejected'
    assert receive(setup)['state'] == 'rejected'
    assert setup.state['calls'] == 1
    setup.store.put_subscription(setup.sub)
    result = receive(setup, 'retry', payload='3' * 32)
    assert result['state'] == 'done'
    assert receive(setup, 'retry', payload='3' * 32) == result
    assert setup.state['calls'] == 2
    assert setup.state['bytes'][0] == setup.state['bytes'][1]


def test_grant_change_never_adopts_original_event_into_new_execution_authority(setup):
    setup.state['mode'] = 'before'
    assert receive(setup)['state'] == 'interrupted'
    setup.store.put_subscription({**setup.sub, 'grant': '4' * 32})
    with pytest.raises(PermissionError):
        receive(setup)
    assert setup.state['calls'] == 1


def test_revocation_between_claim_and_ingress_leaves_unknown_without_event(setup):
    setup.state['mode'] = 'revoke'
    with pytest.raises(PermissionError):
        receive(setup)
    key, _ = setup.publications.log._keys(setup.binding, 'order-1')
    assert setup.store.graph.get_doc(key)['state'] == 'interrupted'
    assert setup.store.get('subscription', setup.sub['id'], setup.sub['scope'])['next_sequence'] == 1


def test_alternate_sealed_device_and_source_binding_cannot_publish(setup):
    setup.caller = {**setup.caller, 'certificate': 'different-enrolled-device'}
    with pytest.raises(PermissionError):
        receive(setup)
    assert setup.state['calls'] == 0


def test_source_reconfiguration_cannot_reuse_old_business_event_identity(setup):
    assert receive(setup)['state'] == 'done'
    setup.binding = {**setup.binding, 'generation': '5' * 32}
    actual = setup.store.get('source', setup.binding['source'], setup.binding['scope'])
    setup.store.store.put('source', {**actual, 'generation': setup.binding['generation']})
    setup.store.graph.put_doc(Connections._key(actual['id']), {'binding': setup.binding, 'phase': 'ready'})
    with pytest.raises(Conflict):
        receive(setup)
    assert setup.state['calls'] == 1

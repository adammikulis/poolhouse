"""Source custody and owner CAS precede process activation; retries only inspect."""
import copy
from types import SimpleNamespace

import pytest

from poolhouse.fleet import studio_epoch
from poolhouse.fleet.producer_connections import Connections
from poolhouse.fleet.studio_authority import StudioAuthority
from poolhouse.studio.store import Conflict, StudioStore, digest
from poolhouse.studio.stream_store import StreamStore

CONTEXT = {'pool': 'a' * 64, 'device': 'b' * 64, 'scope': 'scope', 'active': True}
OWNER = {'id': 'current-owner-session', 'scope': 'scope'}
RECEIVER = {'certificate': 'receiver certificate', 'instance': 'c' * 64, 'runtime': 'd' * 64}
OFFER = {'id': 'e' * 32, 'revision': 1, 'state': 'offered', 'label': 'Order software',
         'device_label': 'Producer device', 'process': {'pid': 51, 'started': 101.0},
         'receiver': {'id': 'receiver', 'certificate': RECEIVER['certificate'], 'pool': CONTEXT['pool'],
                      'label': 'Receiver', 'device_label': 'Receiver device'},
         'context': {'pool': CONTEXT['pool'], 'runtime': 'f' * 64, 'daemon_instance': '1' * 64,
                     'device_certificate': 'producer certificate'}, 'implementation': '2' * 64}
BODY = {'kind': 'webhook', 'provider': 'software-producer', 'label': 'Order source',
        'producer': {'id': OFFER['id'], 'revision': 1}, 'idempotency_key': 'new-source'}


class Transport:
    def __init__(self):
        self.calls, self.binding, self.active = [], None, False
        self.after_prepare = self.after_activate = lambda: None

    def inspect(self, ident, revision):
        self.calls.append('inspect')
        assert ident == OFFER['id'] and revision == 1
        return copy.deepcopy(OFFER)

    def _authorize(self, intent, stage):
        assert not self.connections.store.lock._is_owned()
        challenge = '3' * 64
        result = self.connections.authorize({'certificate': OFFER['context']['device_certificate'], 'pool': CONTEXT['pool']},
            {'source': intent['source'], 'command_id': intent['command_id'], 'stage': stage,
             'challenge': challenge, 'binding_digest': None if stage == 'prepare' else digest(self.binding)})
        assert result['authorized'] is True and result['challenge'] == challenge
        return result

    def prepare(self, intent):
        self.calls.append('prepare')
        self._authorize(intent, 'prepare')
        self.binding = {'source': intent['source'], 'generation': intent['generation'],
            'scope': CONTEXT['scope'], 'epoch': intent['epoch'], 'pool': CONTEXT['pool'],
            'receiver': RECEIVER['certificate'], 'producer': OFFER['context']['device_certificate'],
            'instance': OFFER['context']['daemon_instance'], 'runtime': OFFER['context']['runtime'],
            'implementation': OFFER['implementation'], 'sdk': OFFER['process'],
            'bridge': {'pid': 52, 'started': 102.0}, 'listener': {'path': '/private/owned/data.sock', 'dev': 1, 'ino': 2},
            'activation': '4' * 64}
        self.after_prepare()
        return {'binding': copy.deepcopy(self.binding), 'active': self.active}

    def activate(self, intent):
        self.calls.append('activate')
        assert intent['phase'] == 'activation_started'
        self._authorize(intent, 'ready')
        self.active = True
        self.after_activate()
        return {'binding_digest': digest(self.binding), 'active': True}

    def status(self, intent):
        assert not self.connections.store.lock._is_owned()
        self.calls.append('status')
        return {'binding': copy.deepcopy(self.binding), 'active': self.active}


@pytest.fixture
def setup(tmp_path):
    state = {'origin': 'launch-ticket', 'context': copy.deepcopy(CONTEXT), 'receiver': copy.deepcopy(RECEIVER),
             'custody': True, 'custody_principals': []}
    store = StudioStore(tmp_path / 'studio.ladybug')
    streams = StreamStore(store)
    authority = StudioAuthority(tmp_path / 'authority', current=lambda: state['context'],
        owner=lambda principal: state['origin'] if principal == OWNER else '', resources=lambda *args: True)
    service = SimpleNamespace(authority=authority, store=streams)
    transport = Transport()
    def custody(principal, intent, binding):
        state['custody_principals'].append(principal)
        return state['custody']
    connections = Connections(service, transport, identity=lambda: state['receiver'], custody=custody)
    transport.connections = connections
    yield connections, transport, state
    authority.close()
    store.close()


def intent(connections, source):
    return connections.store.graph.get_doc(connections._key(source['id']))


def test_source_stays_paused_and_activation_follows_custody_owner_cas(setup):
    connections, transport, state = setup
    source = connections.register(OWNER, BODY)
    assert transport.calls == ['inspect', 'prepare', 'activate']
    assert intent(connections, source)['phase'] == 'ready'
    assert connections.configured(source['id'], source['generation']) is True
    assert connections.store.indexed('subscription', OWNER['scope']) == []
    assert source['available'] is False  # Actual liveness is derived, never persisted as authority.
    assert source['revision'] == 1
    assert state['custody_principals'] == [OWNER]
    assert 'principal' not in intent(connections, source)


def test_repeated_create_inspects_original_process_without_provisioning_again(setup):
    connections, transport, _ = setup
    source = connections.register(OWNER, BODY)
    assert connections.register(OWNER, BODY) == source
    assert transport.calls == ['inspect', 'prepare', 'activate', 'status']


def test_reused_key_with_changed_producer_or_label_conflicts(setup):
    connections, transport, _ = setup
    connections.register(OWNER, BODY)
    for changed in ({**BODY, 'label': 'Changed'}, {**BODY, 'producer': {'id': '5' * 32, 'revision': 1}}):
        with pytest.raises(Conflict):
            connections.register(OWNER, changed)
    assert transport.calls.count('prepare') == 1


@pytest.mark.parametrize('origin', ['', 'invite', 'token', 'setup'])
def test_weak_owner_cannot_inspect_offer_or_mint_intent(setup, origin):
    connections, transport, state = setup
    state['origin'] = origin
    with pytest.raises(PermissionError):
        connections.register(OWNER, BODY)
    assert transport.calls == []
    assert connections.store.indexed('source', OWNER['scope']) == []


@pytest.mark.parametrize('field', ['certificate', 'pool'])
def test_offer_for_another_receiver_or_pool_never_mints_source(setup, field):
    connections, transport, _ = setup
    offer = copy.deepcopy(OFFER)
    offer['receiver'][field] = 'another'
    transport.inspect = lambda *args: offer
    with pytest.raises(PermissionError):
        connections.register(OWNER, BODY)
    assert connections.store.indexed('source', OWNER['scope']) == []


def test_revocation_after_prepare_prevents_activation(setup):
    connections, transport, _ = setup
    transport.after_prepare = lambda: studio_epoch.invalidate(connections.authority.root, [CONTEXT['pool']], 'security-reset')
    with pytest.raises(PermissionError):
        connections.register(OWNER, BODY)
    assert transport.calls == ['inspect', 'prepare']
    source = connections.store.indexed('source', OWNER['scope'])[0]
    assert connections.configured(source['id'], source['generation']) is False
    assert intent(connections, source)['phase'] == 'prepare_started'


def test_owner_session_loss_or_receiver_change_after_prepare_never_activates(setup):
    connections, transport, state = setup
    transport.after_prepare = lambda: state.update(origin='')
    with pytest.raises(PermissionError):
        connections.register(OWNER, BODY)
    assert 'activate' not in transport.calls


@pytest.mark.parametrize('custody', [False, 1, 'yes'])
def test_custody_requires_exact_true_before_activation(setup, custody):
    connections, transport, state = setup
    state['custody'] = custody
    with pytest.raises(PermissionError):
        connections.register(OWNER, BODY)
    assert 'activate' not in transport.calls


def test_lost_prepare_ack_recovers_exact_inert_process_without_respawn(setup):
    connections, transport, _ = setup
    transport.after_prepare = lambda: (_ for _ in ()).throw(OSError('lost response'))
    source = connections.register(OWNER, BODY)
    assert intent(connections, source)['phase'] == 'prepare_started'
    source = connections.check(OWNER, source, {'revision': 1, 'idempotency_key': BODY['idempotency_key']})
    assert transport.calls == ['inspect', 'prepare', 'status', 'activate']
    assert intent(connections, source)['phase'] == 'ready'


def test_lost_activation_ack_only_checks_and_never_repeats_activation(setup):
    connections, transport, _ = setup
    transport.after_activate = lambda: (_ for _ in ()).throw(OSError('lost response'))
    source = connections.register(OWNER, BODY)
    assert intent(connections, source)['phase'] == 'activation_started'
    connections.check(OWNER, source, {'revision': 1, 'idempotency_key': BODY['idempotency_key']})
    assert transport.calls == ['inspect', 'prepare', 'activate', 'status']
    assert intent(connections, source)['phase'] == 'ready'


def test_unknown_inactive_activation_never_repeats_or_starts_another_process(setup):
    connections, transport, _ = setup
    transport.activate = lambda intent: (_ for _ in ()).throw(OSError('outcome unknown'))
    source = connections.register(OWNER, BODY)
    assert intent(connections, source)['phase'] == 'activation_started'
    for _ in range(2):
        connections.check(OWNER, source, {'revision': 1, 'idempotency_key': BODY['idempotency_key']})
    assert transport.calls == ['inspect', 'prepare', 'status', 'status']
    assert intent(connections, source)['phase'] == 'activation_started'


def test_pre_cas_active_response_is_not_adopted(setup):
    connections, transport, _ = setup
    transport.active = True
    with pytest.raises(PermissionError):
        connections.register(OWNER, BODY)
    assert transport.calls == ['inspect', 'prepare']


def test_alternate_member_cannot_read_owner_intent(setup):
    connections, _, _ = setup
    source = connections.register(OWNER, BODY)
    record = intent(connections, source)
    request = {'source': source['id'], 'command_id': record['command_id'], 'stage': 'current',
               'challenge': '3' * 64, 'binding_digest': digest(record['binding'])}
    for producer in ({'certificate': 'other', 'pool': CONTEXT['pool']},
                     {'certificate': OFFER['context']['device_certificate'], 'pool': '6' * 64}):
        with pytest.raises(PermissionError):
            connections.authorize(producer, request)


def test_old_stage_or_wrong_binding_never_authorizes(setup):
    connections, _, _ = setup
    source = connections.register(OWNER, BODY)
    record = intent(connections, source)
    request = {'source': source['id'], 'command_id': record['command_id'], 'stage': 'current',
               'challenge': '3' * 64, 'binding_digest': digest(record['binding'])}
    producer = {'certificate': OFFER['context']['device_certificate'], 'pool': CONTEXT['pool']}
    for changes in ({'stage': 'prepare', 'binding_digest': None}, {'binding_digest': '0' * 64}, {'command_id': '0' * 64}):
        with pytest.raises(PermissionError):
            connections.authorize(producer, {**request, **changes})


def test_check_requires_original_request_and_source_revision(setup):
    connections, _, _ = setup
    source = connections.register(OWNER, BODY)
    for changes in ({'revision': 2}, {'revision': True}, {'idempotency_key': 'different'},
                    {'idempotency_key': True}, {'idempotency_key': ''}):
        with pytest.raises(Conflict):
            connections.check(OWNER, source, {'revision': 1, 'idempotency_key': BODY['idempotency_key'], **changes})


def test_offer_response_bool_revision_is_not_an_owner_cas(setup):
    connections, transport, _ = setup
    transport.inspect = lambda *args: {**copy.deepcopy(OFFER), 'revision': True}
    with pytest.raises(PermissionError):
        connections.register(OWNER, BODY)
    assert connections.store.indexed('source', OWNER['scope']) == []


@pytest.mark.parametrize('field,value', [('source', []), ('source', '../source'), ('command_id', True),
                                       ('binding_digest', []), ('challenge', True), ('stage', []),
                                       ('extra', 'not allowed')])
def test_authorization_challenge_has_strict_exact_schema(setup, field, value):
    connections, _, _ = setup
    source = connections.register(OWNER, BODY)
    record = intent(connections, source)
    request = {'source': source['id'], 'command_id': record['command_id'], 'stage': 'current',
               'challenge': '3' * 64, 'binding_digest': digest(record['binding'])}
    with pytest.raises(ValueError):
        connections.authorize({'certificate': OFFER['context']['device_certificate'], 'pool': CONTEXT['pool']},
                              {**request, field: value})

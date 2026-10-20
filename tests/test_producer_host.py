"""Owner-intent host composition with real store/offer checks and a controlled child boundary."""
import copy
import os
import threading
from types import SimpleNamespace

import pytest

from poolhouse.fleet import producer_host, tls
from poolhouse.fleet.job_records import process_started
from poolhouse.fleet.producer_bridge import signed_request
from poolhouse.fleet.producer_offers import OfferRegistry
from poolhouse.studio.store import Conflict, StudioStore, digest
from poolhouse.studio.stream_store import StreamStore


@pytest.fixture
def setup(tmp_path, monkeypatch):
    store = StudioStore(tmp_path / 'studio.db')
    service = SimpleNamespace(store=StreamStore(store))
    producer, receiver = tls.identity(tmp_path / 'producer', 'producer'), tls.identity(tmp_path / 'receiver', 'receiver')
    sdk = {'pid': os.getpid(), 'started': process_started(os.getpid())}
    context = {'pool': 'a' * 64, 'runtime': 'b' * 64, 'daemon_instance': 'c' * 64,
               'device_certificate': producer.beacon}
    target = {'id': 'receiver', 'certificate': receiver.beacon, 'pool': context['pool'],
              'label': 'Receiver app', 'device_label': 'Receiver device'}
    state = {'alive': True, 'allowed': True, 'creates': 0, 'forwards': 0}
    registry = OfferRegistry(context=lambda: copy.deepcopy(context), receivers=lambda: [copy.deepcopy(target)],
        alive=lambda process: state['alive'] and process == sdk, implementation='d' * 64, device_label='Producer device')
    offer = registry.offer(sdk, {'receiver_id': target['id'], 'label': 'Orders software'})
    body = {'source': 'e' * 32, 'command_id': 'f' * 64, 'offer_id': offer['id'], 'revision': 1}
    actual = {'certificate': receiver.beacon, 'pool': context['pool']}
    intent = {'source': body['source'], 'command_id': body['command_id'], 'generation': '1' * 32,
              'epoch': '2' * 64, 'context': {'pool': context['pool'], 'scope': 'receiver-scope',
                  'device': receiver.fingerprint, 'active': True}, 'receiver': {'certificate': receiver.beacon},
              'offer': copy.deepcopy(registry.inspect(offer['id'], 1)), 'binding': None,
              'revision': 1, 'request_key': 'owner-command', 'phase': 'prepare_started'}
    control = {'certificate': producer.beacon, 'control_path': str(tmp_path / 'control.sock'),
               'daemon': {**sdk, 'instance': context['daemon_instance']}, 'pool': context['pool'], 'runtime': context['runtime']}

    class Transport:
        def control(self):
            return copy.deepcopy(control)

        def request(self, caller, action, request):
            assert not store.lock._is_owned(), 'network under durable store lock'
            assert caller == actual
            if not state['allowed']:
                raise PermissionError('receiver revoked')
            if action == 'producer-authorize':
                if request['binding_digest'] is not None:
                    assert request['binding_digest'] == digest(intent['binding'])
                return {'authorized': True, 'challenge': request['challenge'], 'stage': request['stage'],
                        'intent': copy.deepcopy(intent)}
            state['forwards'] += 1
            return {'state': 'done', 'id': request['event_id']}

    class Child:
        def __init__(self, root, *, sdk, control, current):
            assert not store.lock._is_owned()
            state['creates'] += 1
            self.current, self.closed = current, False
            self.process = {'pid': 75001, 'started': 1700000000.0}

        def prepare(self):
            assert self.current() is True
            return {'process': self.process, 'path': '/private/source.sock', 'inode': {'dev': 4, 'ino': 5}, 'nonce': '3' * 64}

        def matches(self, process):
            return not self.closed and process == self.process

        def activate(self, binding, secret):
            hook = state.get('activate')
            if hook:
                hook()
            return digest(binding)

        def close(self):
            self.closed = True
            return True

    monkeypatch.setattr(producer_host, 'OwnedProducer', Child)
    host = producer_host.ProducerHost(tmp_path, service, registry, Transport())
    yield SimpleNamespace(host=host, service=service, registry=registry, transport=host.transport, body=body,
                          receiver=actual, intent=intent, state=state, control=control)
    host.close()
    store.close()


def prepared(setup):
    response = setup.host.prepare(setup.receiver, setup.body)
    assert response['active'] is False
    setup.intent['binding'] = copy.deepcopy(response['binding'])
    return response['binding']


def activated(setup):
    binding = prepared(setup)
    command = {key: setup.body[key] for key in ('source', 'command_id')}
    assert setup.host.activate(setup.receiver, command) == {'binding_digest': digest(binding), 'active': True}
    return binding, command


def test_durable_duplicate_prepare_attaches_one_inert_child(setup):
    first = setup.host.prepare(setup.receiver, setup.body)
    assert setup.host.prepare(setup.receiver, setup.body) == first
    assert setup.state['creates'] == 1
    assert not setup.host.accepts(first['binding']['bridge'])


def test_restart_cannot_adopt_or_repeat_durable_possible_launch(setup):
    prepared(setup)
    restarted = producer_host.ProducerHost(setup.host.root, setup.service, setup.registry, setup.transport)
    with pytest.raises(InterruptedError, match='adopted or repeated'):
        restarted.prepare(setup.receiver, setup.body)
    assert setup.state['creates'] == 1


def test_actual_owner_custody_binding_precedes_activation_and_data(setup):
    binding, command = activated(setup)
    row = next(iter(setup.host.rows.values()))
    request = signed_request(row['cap'].secret, binding, 'bridge-publish', 'order-1', b'{"total":17}')
    assert setup.host.bridge(binding['bridge'], 'bridge-publish', request) == {'state': 'done', 'id': 'order-1'}
    assert setup.host.status(setup.receiver, command)['active'] is True
    assert setup.state['forwards'] == 1
    with pytest.raises(PermissionError):
        setup.host.bridge(binding['bridge'], 'bridge-publish', request)
    assert setup.state['forwards'] == 1


def test_sibling_process_cannot_use_valid_capability_bytes(setup):
    binding, _ = activated(setup)
    row = next(iter(setup.host.rows.values()))
    request = signed_request(row['cap'].secret, binding, 'bridge-publish', 'order-1', b'{"total":17}')
    with pytest.raises(PermissionError):
        setup.host.bridge({'pid': 75002, 'started': binding['bridge']['started']}, 'bridge-publish', request)
    assert setup.state['forwards'] == 0


@pytest.mark.parametrize('during', ['before', 'ack'])
def test_revoke_or_late_activation_ack_never_revives_capability(setup, during):
    binding = prepared(setup)
    command = {key: setup.body[key] for key in ('source', 'command_id')}
    if during == 'before':
        setup.host.revoke(setup.receiver, command)
    else:
        setup.state['activate'] = lambda: setup.host.revoke(setup.receiver, command)
    with pytest.raises((PermissionError, InterruptedError)):
        setup.host.activate(setup.receiver, command)
    row = next(iter(setup.host.rows.values()))
    assert row['cap'].active is False and row['cap'].revoked is True
    assert not setup.host.accepts(binding['bridge'])


def test_receiver_revoked_after_child_ack_stops_exact_child(setup):
    prepared(setup)
    setup.state['activate'] = lambda: setup.state.update(allowed=False)
    command = {key: setup.body[key] for key in ('source', 'command_id')}
    with pytest.raises(PermissionError):
        setup.host.activate(setup.receiver, command)
    assert next(iter(setup.host.rows.values()))['owned'].closed is True


def test_withdrawn_offer_or_sdk_exit_denies_future_data(setup):
    binding, _ = activated(setup)
    setup.state['alive'] = False
    assert not setup.host.accepts(binding['bridge'])
    row = next(iter(setup.host.rows.values()))
    request = signed_request(row['cap'].secret, binding, 'bridge-receipt', 'order-1')
    with pytest.raises(PermissionError):
        setup.host.bridge(binding['bridge'], 'bridge-receipt', request)
    assert setup.state['forwards'] == 0


def test_changed_owner_generation_under_same_command_conflicts(setup):
    prepared(setup)
    setup.intent['epoch'] = '4' * 64
    with pytest.raises(Conflict):
        setup.host.prepare(setup.receiver, setup.body)
    assert setup.state['creates'] == 1


def test_context_change_during_authorization_cannot_spawn(setup, monkeypatch):
    original = setup.transport.request
    def changed(*args):
        result = original(*args)
        setup.registry.anchor = {**setup.registry.anchor, 'runtime': '5' * 64}
        return result
    monkeypatch.setattr(setup.transport, 'request', changed)
    with pytest.raises(PermissionError):
        setup.host.prepare(setup.receiver, setup.body)
    assert setup.state['creates'] == 0


def test_dead_owned_child_is_never_reported_available(setup):
    _, command = activated(setup)
    next(iter(setup.host.rows.values()))['owned'].close()
    with pytest.raises(InterruptedError):
        setup.host.status(setup.receiver, command)


def test_offer_context_must_match_actual_control_daemon_before_spawn(setup):
    setup.control['runtime'] = '6' * 64
    setup.host.control = copy.deepcopy(setup.control)
    with pytest.raises(PermissionError):
        setup.host.prepare(setup.receiver, setup.body)
    assert setup.state['creates'] == 0


def test_prepare_replay_after_owner_custody_and_active_attaches_same_child(setup):
    binding, command = activated(setup)
    setup.intent['phase'] = 'ready'
    assert setup.host.prepare(setup.receiver, setup.body) == {'binding': binding, 'active': True}
    assert setup.host.activate(setup.receiver, command) == {'binding_digest': digest(binding), 'active': True}
    assert setup.state['creates'] == 1


def test_replayed_prepare_and_active_ack_recheck_receiver_revocation(setup):
    _, command = activated(setup)
    setup.state['allowed'] = False
    with pytest.raises(PermissionError):
        setup.host.prepare(setup.receiver, setup.body)
    with pytest.raises(PermissionError):
        setup.host.activate(setup.receiver, command)
    assert setup.state['creates'] == 1


def test_inspect_cannot_attach_changed_actual_child_binding(setup):
    binding = prepared(setup)
    setup.intent['phase'] = 'authorized'
    setup.intent['binding'] = {**binding, 'activation': '7' * 64}
    with pytest.raises(Conflict):
        setup.host.prepare(setup.receiver, setup.body)
    assert setup.state['creates'] == 1


def test_one_software_offer_cannot_spawn_for_a_second_source_command(setup):
    prepared(setup)
    replacement = {**setup.body, 'source': '8' * 32, 'command_id': '9' * 64}
    setup.intent.update(source=replacement['source'], command_id=replacement['command_id'], binding=None)
    with pytest.raises(Conflict, match='already belongs'):
        setup.host.prepare(setup.receiver, replacement)
    assert setup.state['creates'] == 1
    assert setup.host.consumed(setup.body['offer_id']) is True


def test_consumed_offer_stays_terminal_after_explicit_revoke_and_restart(setup):
    _, command = activated(setup)
    setup.host.revoke(setup.receiver, command)
    replacement = {**setup.body, 'source': '8' * 32, 'command_id': '9' * 64}
    setup.intent.update(source=replacement['source'], command_id=replacement['command_id'], binding=None)
    restarted = producer_host.ProducerHost(setup.host.root, setup.service, setup.registry, setup.transport)
    with pytest.raises(Conflict, match='already belongs'):
        restarted.prepare(setup.receiver, replacement)
    assert setup.state['creates'] == 1


def test_concurrent_source_commands_cannot_share_one_software_offer(setup, monkeypatch):
    ready = threading.Barrier(2)
    original = setup.transport.request
    def authorize(caller, action, request):
        reply = original(caller, action, request)
        reply['intent'].update(source=request['source'], command_id=request['command_id'])
        ready.wait(timeout=5)
        return reply
    monkeypatch.setattr(setup.transport, 'request', authorize)
    results = []
    def prepare(body):
        try:
            results.append(setup.host.prepare(setup.receiver, body))
        except Conflict:
            results.append('conflict')
    different = {**setup.body, 'source': '8' * 32, 'command_id': '9' * 64}
    threads = [threading.Thread(target=prepare, args=(body,)) for body in (setup.body, different)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len(results) == 2 and results.count('conflict') == 1
    assert setup.state['creates'] == 1


def test_existing_command_without_exact_offer_index_cannot_attach_on_inner_path(setup, monkeypatch):
    prepared(setup)
    setup.service.store.graph.delete_doc('studio.producer-offer-use:' + setup.body['offer_id'])
    monkeypatch.setattr(setup.host, '_retained', lambda *args: None)
    with pytest.raises(PermissionError, match='offer intent'):
        setup.host.prepare(setup.receiver, setup.body)
    assert setup.state['creates'] == 1

"""Software offers are live process evidence, never source-write authority."""
import copy
import threading

import pytest

from poolhouse.fleet.producer_offers import OfferRegistry
from poolhouse.studio.store import Conflict

PROCESS = {'pid': 41, 'started': 101.0}
RECEIVER = {'id': 'receiver', 'certificate': 'public receiver certificate', 'pool': 'a' * 64,
            'label': 'Receiver', 'device_label': 'Compute device'}
CONTEXT = {'pool': 'a' * 64, 'runtime': 'b' * 64, 'daemon_instance': 'c' * 64,
           'device_certificate': 'public producer certificate'}
REQUEST = {'receiver_id': 'receiver', 'label': 'Order software'}


@pytest.fixture
def setup():
    state = {'context': copy.deepcopy(CONTEXT), 'receivers': [copy.deepcopy(RECEIVER)], 'alive': True}
    registry = OfferRegistry(context=lambda: state['context'], receivers=lambda: state['receivers'],
                             alive=lambda process: state['alive'], implementation='d' * 64,
                             device_label='Producer device')
    return registry, state


def test_identical_offer_attaches_without_authority_or_spawn(setup):
    registry, _ = setup
    row = registry.offer(PROCESS, REQUEST)
    assert registry.offer(PROCESS, REQUEST) == row
    assert registry.status(PROCESS, row['id']) == row
    assert registry.inspect(row['id'], row['revision']) == row
    public = registry.discover(RECEIVER)
    assert public == [{'id': row['id'], 'revision': 1, 'label': REQUEST['label'],
                       'software_label': REQUEST['label'], 'device_label': 'Producer device',
                       'available': True, 'reason': ''}]
    assert not {'process', 'context', 'certificate', 'capability', 'source'} & public[0].keys()


def test_concurrent_offers_attach_once(setup):
    registry, _ = setup
    results = []
    threads = [threading.Thread(target=lambda: results.append(registry.offer(PROCESS, REQUEST))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results[0] == results[1]
    assert len(registry.rows) == 1


def test_changed_label_conflicts_and_withdrawn_offer_never_revives(setup):
    registry, _ = setup
    row = registry.offer(PROCESS, REQUEST)
    with pytest.raises(Conflict):
        registry.offer(PROCESS, {**REQUEST, 'label': 'Changed'})
    withdrawn = registry.withdraw(PROCESS, row['id'])
    assert registry.withdraw(PROCESS, row['id']) == withdrawn
    assert registry.offer(PROCESS, REQUEST) == withdrawn
    assert registry.discover(RECEIVER) == []
    with pytest.raises(PermissionError):
        registry.inspect(row['id'], 1)


@pytest.mark.parametrize('process', [{'pid': 42, 'started': 101.0}, {'pid': 41, 'started': 102.0}])
def test_foreign_or_reused_pid_cannot_read_or_withdraw(setup, process):
    registry, _ = setup
    row = registry.offer(PROCESS, REQUEST)
    for method in (registry.status, registry.withdraw):
        with pytest.raises(PermissionError):
            method(process, row['id'])
    assert registry.inspect(row['id'], 1) == row


@pytest.mark.parametrize('value', [False, 1, 'yes', None])
def test_process_liveness_requires_exact_true(setup, value):
    registry, state = setup
    state['alive'] = value
    with pytest.raises(PermissionError):
        registry.offer(PROCESS, REQUEST)
    assert registry.rows == {}


@pytest.mark.parametrize('field,value', [('pool', 'e' * 64), ('runtime', 'e' * 64),
                                         ('daemon_instance', 'e' * 64), ('device_certificate', 'changed certificate')])
def test_context_change_invalidates_old_process_evidence(setup, field, value):
    registry, state = setup
    row = registry.offer(PROCESS, REQUEST)
    state['context'][field] = value
    with pytest.raises(PermissionError):
        registry.inspect(row['id'], 1)
    with pytest.raises(PermissionError):
        registry.offer(PROCESS, REQUEST)


def test_exit_hides_offer_and_restart_does_not_adopt_it(setup):
    registry, state = setup
    row = registry.offer(PROCESS, REQUEST)
    state['alive'] = False
    assert registry.discover(RECEIVER) == []
    with pytest.raises(PermissionError):
        registry.inspect(row['id'], 1)
    restarted = OfferRegistry(context=lambda: CONTEXT, receivers=lambda: [RECEIVER],
                              alive=lambda process: True, implementation='d' * 64,
                              device_label='Producer device')
    with pytest.raises(Conflict):
        restarted.inspect(row['id'], 1)


@pytest.mark.parametrize('change', ['certificate', 'missing', 'duplicate', 'pool'])
def test_current_receiver_is_exact_unique_enrolled_snapshot(setup, change):
    registry, state = setup
    row = registry.offer(PROCESS, REQUEST)
    if change == 'missing':
        state['receivers'] = []
    elif change == 'duplicate':
        state['receivers'].append(copy.deepcopy(RECEIVER))
    else:
        state['receivers'][0][change] = 'changed'
    with pytest.raises(PermissionError):
        registry.inspect(row['id'], 1)


def test_wrong_intended_receiver_and_stale_revision_do_not_enroll(setup):
    registry, _ = setup
    row = registry.offer(PROCESS, REQUEST)
    with pytest.raises(PermissionError):
        registry.discover({**RECEIVER, 'certificate': 'other'})
    with pytest.raises(Conflict):
        registry.inspect(row['id'], 2)
    with pytest.raises(Conflict):
        registry.inspect(row['id'], True)


def test_revocation_during_creation_leaves_no_offer(setup):
    registry, state = setup
    calls = []

    def alive(process):
        calls.append(process)
        if len(calls) == 2:
            state['context']['daemon_instance'] = 'e' * 64
            return False
        return True

    registry.alive = alive
    with pytest.raises(PermissionError):
        registry.offer(PROCESS, REQUEST)
    assert registry.rows == registry.keys == {}


@pytest.mark.parametrize('process', [{'pid': True, 'started': 1}, {'pid': 1, 'started': float('nan')},
                                    {'pid': 1, 'started': float('inf')}, {'pid': 1, 'started': True},
                                    {'pid': 1, 'started': 0}, {'pid': 1, 'started': 1, 'owner': True}])
def test_process_body_has_strict_transport_schema(setup, process):
    registry, _ = setup
    with pytest.raises(ValueError):
        registry.offer(process, REQUEST)


def test_payload_cannot_select_code_endpoint_or_process(setup):
    registry, _ = setup
    for key in ('url', 'module', 'process', 'source', 'owner'):
        with pytest.raises(ValueError):
            registry.offer(PROCESS, {**REQUEST, key: 'injected'})
    row = registry.offer(PROCESS, {**REQUEST, 'label': '<img onerror="change source">'})
    assert registry.discover(RECEIVER)[0]['label'] == row['label']


def test_returned_private_and_public_views_cannot_mutate_registry(setup):
    registry, _ = setup
    row = registry.offer(PROCESS, REQUEST)
    row['process']['pid'] = 99
    listed = registry.receivers_for(PROCESS)
    listed[0]['certificate'] = 'altered'
    assert registry.status(PROCESS, row['id'])['process'] == PROCESS

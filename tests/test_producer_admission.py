"""Receiver transactions, not transport errors, prove exact producer non-admission."""
import contextlib
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from poolhouse.fleet.producer_admission import Admission
from poolhouse.studio.producer_log import ProducerLog
from poolhouse.studio.store import Conflict, StudioStore, digest
from poolhouse.studio.stream_store import StreamStore

RAW = b'{"value":17,"note":"inert data"}'


@pytest.fixture
def setup(tmp_path):
    base = StudioStore(tmp_path / 'studio.ladybug')
    store = StreamStore(base)
    owner = {'id': 'owner', 'scope': 'scope'}
    source = store.create('source', owner, {'kind': 'webhook', 'provider': 'software-producer',
                                          'generation': 'b' * 32}, 'source')
    principal = {'id': 'service-grant', 'scope': 'scope', 'grant_id': 'g' * 32}
    sub = store.create('subscription', owner, {'source_id': source['id'], 'input_node': 'record',
                       'output_nodes': ['record'], 'execution_output_nodes': ['record']}, 'sub')
    sub.update(state='active', grant=principal['grant_id'], service_principal=principal)
    store.put_subscription(sub)
    binding = {'source': source['id'], 'generation': source['generation'], 'scope': source['scope'],
               'subscription': sub['id'], 'grant': sub['grant'], 'epoch': 'c' * 64, 'pool': 'd' * 64}
    state = {'current': True, 'historical': True, 'grant': True}
    service = SimpleNamespace(store=store, authorized=lambda *_: state['grant'])
    service.ingest = lambda subscription, event: store.admit(subscription, event, principal, service.authorized)
    admission = Admission(service, current=lambda actual: state['current'] if actual == binding else False,
                          receipt_current=lambda row: state['historical'] if row['binding'] == binding else False)
    log = ProducerLog(base, current=admission.current, receipt=admission.receipt, negative=admission.negative)
    yield SimpleNamespace(base=base, store=store, sub=sub, source=source, principal=principal,
                          binding=binding, state=state, service=service, admission=admission, log=log)
    base.close()


def claim(setup, event='event-1'):
    setup.log.stage(setup.binding, event, RAW)
    row, acquired = setup.log.claim(setup.binding, event)
    assert acquired
    return row


def incoming(row):
    return {'id': row['event_id'], 'generation': row['binding']['generation'],
            'payload': json.loads(row['raw']), 'digest': row['wire_digest'], 'attempt': digest(row['attempt'])}


def busy(setup):
    actual = setup.store.get('subscription', setup.sub['id'], 'scope')
    actual['inflight'] = 'previous-record'
    setup.store.put_subscription(actual)


def free(setup):
    actual = setup.store.get('subscription', setup.sub['id'], 'scope')
    actual['inflight'] = None
    setup.store.put_subscription(actual)


def test_actual_inbox_receipt_wins_after_lost_ack_and_execution_revocation(setup):
    row = claim(setup)
    event = setup.admission.admit(setup.sub, incoming(row))
    setup.log.interrupt(setup.binding, row['event_id'], row['attempt'])
    setup.state['current'] = False
    actual = setup.store.get('subscription', setup.sub['id'], 'scope')
    actual.update(state='paused', grant='changed-grant')
    setup.store.put_subscription(actual)
    assert setup.admission.admit(setup.sub, incoming(row)) == event
    assert setup.log.reconcile(setup.binding, row['event_id'])['state'] == 'done'
    assert setup.admission.negative(row) is None
    assert setup.store.get('subscription', setup.sub['id'], 'scope')['next_sequence'] == 2


def test_capacity_marker_commits_before_429_and_remains_terminal_when_capacity_frees(setup):
    row = claim(setup)
    busy(setup)
    with setup.store.lock, setup.store.graph.transaction():
        result = setup.admission.admit(setup.sub, incoming(row))
        assert result == {'rejected': True}
    with pytest.raises(BlockingIOError):
        if result == {'rejected': True}:
            raise BlockingIOError('after commit')
    assert setup.admission.negative(row) == setup.admission._proof(row)
    free(setup)
    assert setup.admission.admit(setup.sub, incoming(row)) == {'rejected': True}
    assert setup.store.get('subscription', setup.sub['id'], 'scope')['next_sequence'] == 1
    rejected = setup.log.reject(setup.binding, row['event_id'], row['attempt'])
    assert rejected['state'] == 'rejected' and rejected['not_sent'] is False


def test_raising_inside_outer_transaction_does_not_forge_durable_negative(setup):
    row = claim(setup)
    busy(setup)
    with pytest.raises(BlockingIOError), setup.store.lock, setup.store.graph.transaction():
        assert setup.admission.admit(setup.sub, incoming(row)) == {'rejected': True}
        raise BlockingIOError('rolled back outer transaction')
    assert setup.admission.negative(row) is None
    with pytest.raises(Conflict):
        setup.log.reject(setup.binding, row['event_id'], row['attempt'])


def test_explicit_retry_new_attempt_cannot_replay_old_hmac_nonce(setup):
    original = claim(setup)
    busy(setup)
    setup.admission.admit(setup.sub, incoming(original))
    setup.log.reject(setup.binding, original['event_id'], original['attempt'])
    setup.log.retry(setup.binding, original['event_id'], '4' * 32)
    assert setup.log.claim(setup.binding, original['event_id'])[1] is False
    row, acquired = setup.log.claim(setup.binding, original['event_id'], retry_key='4' * 32)
    assert acquired and digest(row['attempt']) != digest(original['attempt'])
    free(setup)
    with pytest.raises(PermissionError):
        setup.admission.admit(setup.sub, incoming(original))
    event = setup.admission.admit(setup.sub, incoming(row))
    assert event['state'] == 'queued'
    assert setup.admission.negative(original) is None
    assert setup.log.reconcile(setup.binding, row['event_id'])['receipt']['id'] == event['id']


@pytest.mark.parametrize('change', [{'payload': {'value': 18}}, {'digest': '0' * 64},
                                  {'attempt': '0' * 64}, {'generation': '0' * 32},
                                  {'id': 'other'}, {'record': {}}])
def test_changed_signed_tuple_or_record_never_enters_inbox(setup, change):
    row = claim(setup)
    with pytest.raises((PermissionError, ValueError)):
        setup.admission.admit(setup.sub, {**incoming(row), **change})
    assert setup.store.get('subscription', setup.sub['id'], 'scope')['next_sequence'] == 1
    assert setup.admission.negative(row) is None


@pytest.mark.parametrize('field,value', [('provider', 'other'), ('kind', 'examples'), ('generation', '0' * 32)])
def test_actual_source_is_reloaded_before_new_admission(setup, field, value):
    row = claim(setup)
    source = {**setup.source, field: value}
    setup.base.put('source', source)
    with pytest.raises(PermissionError):
        setup.admission.admit(setup.sub, incoming(row))
    assert setup.admission.negative(row) is None


def test_stale_passed_subscription_cannot_hide_actual_capacity_or_revoked_grant(setup):
    row = claim(setup)
    busy(setup)
    assert setup.admission.admit(setup.sub, incoming(row)) == {'rejected': True}
    setup.log.reject(setup.binding, row['event_id'], row['attempt'])
    setup.log.retry(setup.binding, row['event_id'], '4' * 32)
    row, _ = setup.log.claim(setup.binding, row['event_id'], retry_key='4' * 32)
    free(setup)
    sub = setup.store.get('subscription', setup.sub['id'], 'scope')
    setup.store.put_subscription({**sub, 'grant': 'revoked-grant'})
    with pytest.raises(PermissionError):
        setup.admission.admit(setup.sub, incoming(row))


@pytest.mark.parametrize('busy_source', [False, True])
def test_current_revocation_before_commit_rolls_back_inbox_or_negative(setup, busy_source):
    row = claim(setup)
    if busy_source:
        busy(setup)
    calls = iter([True, False])
    setup.admission.current = lambda _: next(calls)
    with pytest.raises(PermissionError):
        setup.admission.admit(setup.sub, incoming(row))
    assert setup.admission.negative(row) is None
    assert setup.store.get('subscription', setup.sub['id'], 'scope')['next_sequence'] == 1


@pytest.mark.parametrize('allowed', [False, 1, 'yes'])
def test_historical_receipts_require_independent_strict_authority(setup, allowed):
    row = claim(setup)
    setup.admission.admit(setup.sub, incoming(row))
    setup.state['historical'] = allowed
    with pytest.raises(PermissionError):
        setup.admission.receipt(row)
    with pytest.raises(PermissionError):
        setup.admission.negative(row)


def test_concurrent_admission_and_negative_settlement_cannot_lose_accepted_inbox(setup):
    row = claim(setup)
    barrier = threading.Barrier(2)
    results = []
    def admit():
        barrier.wait()
        results.append(setup.admission.admit(setup.sub, incoming(row)))
    def reject():
        barrier.wait()
        with contextlib.suppress(Conflict):
            setup.log.reject(setup.binding, row['event_id'], row['attempt'])
    threads = [threading.Thread(target=admit), threading.Thread(target=reject)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 1 and setup.admission.negative(row) is None
    assert setup.log.reconcile(setup.binding, row['event_id'])['receipt']['id'] == results[0]['id']


@pytest.mark.parametrize('field,value', [('source', 'wrong'), ('generation', 'wrong'),
                                       ('principal', {'grant_id': 'wrong'}), ('envelope', {})])
def test_corrupted_referenced_event_cannot_become_positive_or_negative_proof(setup, field, value):
    row = claim(setup)
    event = setup.admission.admit(setup.sub, incoming(row))
    setup.base.put('stream-event', {**event, field: value})
    with pytest.raises(PermissionError):
        setup.admission.receipt(row)
    with pytest.raises(PermissionError):
        setup.admission.negative(row)


def test_forged_historical_attempt_has_no_receiver_marker_authority(setup):
    row = claim(setup)
    busy(setup)
    setup.admission.admit(setup.sub, incoming(row))
    forged = copy.deepcopy(row)
    forged['attempt']['token'] = '0' * 64
    with pytest.raises(PermissionError):
        setup.admission.negative(forged)


def test_accepted_event_does_not_ack_an_unknown_signed_attempt(setup):
    row = claim(setup)
    setup.admission.admit(setup.sub, incoming(row))
    with pytest.raises(PermissionError):
        setup.admission.admit(setup.sub, {**incoming(row), 'attempt': '0' * 64})


def test_receipt_denial_happens_before_intent_or_inbox_reads(setup, monkeypatch):
    row = claim(setup)
    setup.state['historical'] = False
    monkeypatch.setattr(setup.admission, '_intent', lambda _: pytest.fail('Unauthorized intent read'))
    with pytest.raises(PermissionError):
        setup.admission.receipt(row)
    with pytest.raises(PermissionError):
        setup.admission.negative(row)


@pytest.mark.parametrize('method', ['receipt', 'negative'])
def test_historical_revocation_during_accepted_lookup_never_returns_proof(setup, method):
    row = claim(setup)
    setup.admission.admit(setup.sub, incoming(row))
    calls = iter([True, False])
    setup.admission.receipt_current = lambda _: next(calls)
    with pytest.raises(PermissionError):
        getattr(setup.admission, method)(row)

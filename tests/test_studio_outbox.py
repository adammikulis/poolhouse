"""Crash, replay and current-authority tests for durable output delivery."""
import copy
import threading

import pytest

from poolhouse.studio.outbox import Outbox
from poolhouse.studio.store import Conflict, StudioStore

PRINCIPAL = {'id': 'studio-grant:grant', 'kind': 'studio_subscription', 'grant_id': 'grant', 'scope': 'scope'}
DESTINATION = {'id': 'registered', 'revision': 1, 'url': 'https://receiver.invalid/events'}
EVENT = {'id': 'a' * 64, 'scope': 'scope', 'subscription': 'subscription', 'principal': PRINCIPAL}


@pytest.fixture
def setup(tmp_path):
    store = StudioStore(tmp_path / 'studio.ladybug')
    state = {'authorized': True, 'result': {'state': 'done'}, 'calls': []}
    def send(destination, envelope, *, authorize):
        assert store.get('delivery', envelope['delivery_id'], 'scope')['state'] == 'intent'
        state['calls'].append(copy.deepcopy(envelope))
        assert authorize()
        return state['result']
    outbox = Outbox(store, authorize=lambda row: state['authorized'], send=send)
    yield outbox, store, state
    outbox.close()
    store.close()


def stage(outbox):
    return outbox.stage(EVENT, DESTINATION, {'outputs': [{'choice': 'review', 'text': 'synthetic café'}]})


def request(row, key='action'):
    return {'delivery_id': row['id'], 'request_digest': row['envelope']['request_digest'],
            'destination_revision': row['destination']['revision'], 'idempotency_key': key}


def test_intent_precedes_send_duplicate_attaches_and_changed_body_conflicts(setup):
    outbox, _, state = setup
    row = stage(outbox)
    assert stage(outbox) == row
    assert outbox.attempt(row['id'], 'scope')['state'] == 'done'
    assert outbox.attempt(row['id'], 'scope')['state'] == 'done'
    assert len(state['calls']) == 1
    with pytest.raises(Conflict):
        outbox.stage(EVENT, DESTINATION, {'outputs': 'changed'})
    with pytest.raises(Conflict):
        outbox.stage(EVENT, {**DESTINATION, 'url': 'https://different.invalid'}, row['envelope']['body'])


def test_concurrent_claims_send_only_once(setup):
    outbox, _, state = setup
    row = stage(outbox)
    entered, release = threading.Event(), threading.Event()
    original = outbox.send
    def send(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    outbox.send = send
    first = threading.Thread(target=outbox.attempt, args=(row['id'], 'scope'))
    first.start()
    try:
        assert entered.wait(5)
        assert outbox.attempt(row['id'], 'scope')['state'] == 'intent'
    finally:
        release.set()
        first.join()
    assert len(state['calls']) == 1


def test_uncertain_send_and_restart_never_replay(setup):
    outbox, store, state = setup
    row = stage(outbox)
    def uncertain(*args, **kwargs):
        state['calls'].append('possible-send')
        raise OSError('reply lost')
    outbox.send = uncertain
    assert outbox.attempt(row['id'], 'scope')['state'] == 'interrupted'
    restarted = Outbox(store, authorize=lambda row: True, send=uncertain)
    try:
        assert restarted.attempt(row['id'], 'scope')['state'] == 'interrupted'
        assert state['calls'] == ['possible-send']
    finally:
        restarted.close()


def test_crash_after_intent_is_interrupted_even_if_no_receipt_exists(setup):
    outbox, store, state = setup
    row = stage(outbox)
    row.update(state='intent', attempt={'instance': 'previous', 'token': 'lost'})
    with store.lock, store.graph.transaction():
        outbox._put(row)
    restarted = Outbox(store, authorize=lambda row: True, send=outbox.send)
    try:
        assert restarted.attempt(row['id'], 'scope')['state'] == 'interrupted'
        assert not state['calls']
    finally:
        restarted.close()


def test_unknown_cannot_retry_without_exact_explicit_attestation(setup):
    outbox, _, state = setup
    row = stage(outbox)
    state['result'] = {'state': 'interrupted'}
    row = outbox.attempt(row['id'], 'scope')
    with pytest.raises(PermissionError):
        outbox.retry(PRINCIPAL, row['id'], request(row), owner=lambda *args: True)
    attestation = {**request(row), 'evidence': 'owner_attestation', 'outcome': 'not_delivered'}
    with pytest.raises(PermissionError):
        outbox.reconcile(PRINCIPAL, row['id'], {**attestation, 'destination_revision': 2}, owner=lambda *args: True)
    row = outbox.reconcile(PRINCIPAL, row['id'], attestation, owner=lambda *args: True)
    assert row['state'] == 'blocked' and row['reconciliation']['weaker_than_receiver_receipt']
    assert outbox.reconcile(PRINCIPAL, row['id'], attestation, owner=lambda *args: True) == row
    retry = request(row, 'retry')
    assert outbox.retry(PRINCIPAL, row['id'], retry, owner=lambda *args: True)['state'] == 'queued'
    assert outbox.retry(PRINCIPAL, row['id'], retry, owner=lambda *args: True)['state'] == 'queued'
    state['result'] = {'state': 'done'}
    assert outbox.attempt(row['id'], 'scope')['state'] == 'done'


def test_revoke_before_send_or_after_reply_never_confirms_delivery(setup):
    outbox, _, state = setup
    row = stage(outbox)
    state['authorized'] = False
    blocked = outbox.attempt(row['id'], 'scope')
    assert blocked['state'] == 'blocked' and blocked['not_sent']
    assert outbox.attempt(row['id'], 'scope')['state'] == 'blocked'
    assert not state['calls']
    state['authorized'] = True
    outbox.retry(PRINCIPAL, row['id'], request(row), owner=lambda *args: True)
    def send(*args, **kwargs):
        state['authorized'] = False
        return {'state': 'done'}
    outbox.send = send
    assert outbox.attempt(row['id'], 'scope')['state'] == 'interrupted'


def test_corrupt_pointer_isolated_and_completed_history_not_polled(setup, monkeypatch):
    outbox, store, _ = setup
    row = stage(outbox)
    assert outbox.attempt(row['id'], 'scope')['state'] == 'done'
    store.graph.query("MERGE (a:StudioOutboxActive {id:'missing'}) SET a.scope='scope', a.state='queued'")
    calls = []
    original = store.graph.get_doc
    def get(key, *args):
        calls.append(key)
        return original(key, *args)
    monkeypatch.setattr(store.graph, 'get_doc', get)
    with store.lock:
        assert outbox._indexed('queued') == []
    assert 'studio.delivery:' + row['id'] not in calls
    assert store.graph.get_doc('studio.outbox-index-error:missing')

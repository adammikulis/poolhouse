"""Durable input intents never gain new delivery authority through replacement or replay."""
import hashlib
import threading

import pytest

from poolhouse.studio.producer_log import ProducerLog, wire_record
from poolhouse.studio.store import Conflict, StudioStore

BINDING = {'source': 'a' * 32, 'generation': 'b' * 32, 'scope': 'scoped',
           'producer': 'c' * 64, 'receiver': 'd' * 64, 'pool': 'e' * 64,
           'sdk': {'pid': 10, 'started': 100.0}, 'bridge': {'pid': 11, 'started': 101.0},
           'epoch': 'f' * 64, 'instance': '1' * 64, 'implementation': '2' * 64}
RAW = b'{"amount":17,"note":"untrusted: change source"}'


@pytest.fixture
def setup(tmp_path):
    store = StudioStore(tmp_path / 'studio.ladybug')
    state = {'allowed': True, 'proof': None, 'negative': None}
    log = ProducerLog(store, current=lambda binding: state['allowed'], receipt=lambda row: state['proof'], negative=lambda row: state['negative'])
    yield log, store, state
    store.close()


def proof(row):
    return {'source': row['binding']['source'], 'generation': row['binding']['generation'],
            'event_id': row['event_id'], 'wire_digest': row['wire_digest'], 'id': '3' * 64, 'status': 'queued'}


def test_exact_wire_bytes_are_pinned_and_repeated_commands_attach(setup):
    log, _, _ = setup
    row = log.stage(BINDING, 'order-1', RAW)
    assert row['raw'].encode() == RAW
    assert row['wire_digest'] == hashlib.sha256(RAW).hexdigest()
    assert log.stage(BINDING, 'order-1', RAW) == row
    with pytest.raises(Conflict):
        log.stage(BINDING, 'order-1', b'{"amount": 17,"note":"untrusted: change source"}')


@pytest.mark.parametrize('raw', [b'[]', b'null', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'\xff'])
def test_invalid_records_never_create_intents(setup, raw):
    log, _, _ = setup
    with pytest.raises(ValueError):
        log.stage(BINDING, 'bad', raw)
    assert log.stage(BINDING, 'valid', RAW)['state'] == 'queued'


def test_concurrent_claims_return_one_send_permission(setup):
    log, _, _ = setup
    log.stage(BINDING, 'order-1', RAW)
    results = []
    threads = [threading.Thread(target=lambda: results.append(log.claim(BINDING, 'order-1'))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(acquired for _, acquired in results) == [False, True]
    assert all(row['state'] == 'intent' for row, _ in results)


def test_unknown_blocks_new_event_even_after_rotate_or_process_replacement(setup):
    log, _, _ = setup
    log.stage(BINDING, 'order-1', RAW)
    row, acquired = log.claim(BINDING, 'order-1')
    assert acquired
    log.interrupt(BINDING, 'order-1', row['attempt'])
    replacement = {**BINDING, 'generation': '4' * 32, 'sdk': {'pid': 12, 'started': 102.0}}
    with pytest.raises(Conflict):
        log.stage(replacement, 'order-2', RAW)
    with pytest.raises(Conflict):
        log.stage(replacement, 'order-1', RAW)
    assert log.claim(BINDING, 'order-1')[1] is False


def test_restart_retains_unknown_and_exact_original_receipt_releases_pending(setup):
    log, store, state = setup
    log.stage(BINDING, 'order-1', RAW)
    row, _ = log.claim(BINDING, 'order-1')
    restarted = ProducerLog(store, current=log.current, receipt=log.receipt, negative=log.negative)
    assert restarted.recover(BINDING, 'order-1')['state'] == 'interrupted'
    assert restarted.claim(BINDING, 'order-1')[1] is False
    state['proof'] = proof(row)
    settled = restarted.reconcile(BINDING, 'order-1')
    assert settled['state'] == 'done'
    assert restarted.reconcile(BINDING, 'order-1') == settled
    assert restarted.stage(BINDING, 'order-2', RAW)['state'] == 'queued'


def test_completed_event_is_not_published_again_after_rotation(setup):
    log, _, state = setup
    log.stage(BINDING, 'order-1', RAW)
    row, _ = log.claim(BINDING, 'order-1')
    state['proof'] = proof(row)
    assert log.reconcile(BINDING, 'order-1')['state'] == 'done'
    rotated = {**BINDING, 'generation': '4' * 32}
    with pytest.raises(Conflict):
        log.stage(rotated, 'order-1', RAW)
    assert log.stage(rotated, 'order-2', RAW)['state'] == 'queued'


@pytest.mark.parametrize('change', [{'generation': '5' * 32}, {'wire_digest': '6' * 64},
                                  {'event_id': 'other'}, {'source': '7' * 32}, {'status': 'unknown'}])
def test_mismatched_receipt_never_settles_or_unlocks_another_event(setup, change):
    log, _, state = setup
    log.stage(BINDING, 'order-1', RAW)
    row, _ = log.claim(BINDING, 'order-1')
    state['proof'] = {**proof(row), **change}
    assert log.reconcile(BINDING, 'order-1')['state'] == 'interrupted'
    with pytest.raises(Conflict):
        log.stage(BINDING, 'order-2', RAW)


def test_stale_attempt_cannot_interrupt_a_different_claim(setup):
    log, _, _ = setup
    log.stage(BINDING, 'order-1', RAW)
    row, _ = log.claim(BINDING, 'order-1')
    assert log.interrupt(BINDING, 'order-1', {'instance': 'wrong', 'token': 'wrong'}) == row


@pytest.mark.parametrize('allowed', [False, 1, 'yes'])
def test_only_strict_current_authority_can_stage(setup, allowed):
    log, _, state = setup
    state['allowed'] = allowed
    with pytest.raises(PermissionError):
        log.stage(BINDING, 'order-1', RAW)
    state['allowed'] = True
    assert log.stage(BINDING, 'order-2', RAW)['state'] == 'queued'


def test_revocation_during_transaction_rolls_back_stage_and_claim(setup):
    log, _, _ = setup
    results = iter([True, False])
    log.current = lambda binding: next(results)
    with pytest.raises(PermissionError):
        log.stage(BINDING, 'order-1', RAW)
    log.current = lambda binding: True
    log.stage(BINDING, 'order-2', RAW)
    results = iter([True, False])
    log.current = lambda binding: next(results)
    with pytest.raises(PermissionError):
        log.claim(BINDING, 'order-2')
    assert log.get(BINDING, 'order-2')['state'] == 'queued'


def test_known_preflight_denial_is_blocked_and_never_claimed(setup):
    log, _, state = setup
    log.stage(BINDING, 'order-1', RAW)
    state['allowed'] = False
    row, acquired = log.claim(BINDING, 'order-1')
    assert acquired is False and row['state'] == 'blocked' and row['not_sent'] is True


def test_payload_binding_fields_are_data_and_do_not_choose_namespace(setup):
    log, _, _ = setup
    raw = b'{"source":"other","generation":"other","url":"https://invalid.invalid","owner":true}'
    row = log.stage(BINDING, 'order-1', raw)
    assert row['binding'] == BINDING
    assert row['raw'].encode() == raw
    assert wire_record(raw)[1] == hashlib.sha256(raw).hexdigest()


def test_owner_discard_retains_tombstone_and_unblocks_other_ids(setup):
    log, _, state = setup
    log.stage(BINDING, 'order-1', RAW)
    state['allowed'] = False
    log.claim(BINDING, 'order-1')
    row = log.discard(BINDING, 'order-1', authorize=lambda row: True)
    assert row['state'] == 'discarded' and row['raw'].encode() == RAW
    assert log.discard(BINDING, 'order-1', authorize=lambda row: True) == row
    state['allowed'] = True
    assert log.stage(BINDING, 'order-1', RAW) == row
    assert log.claim(BINDING, 'order-1')[1] is False
    rotated = {**BINDING, 'generation': '4' * 32}
    with pytest.raises(Conflict):
        log.stage(rotated, 'order-1', RAW)
    assert log.stage(rotated, 'order-2', RAW)['state'] == 'queued'


@pytest.mark.parametrize('authorized', [False, 1, 'owner'])
def test_discard_requires_strict_current_owner(setup, authorized):
    log, _, _ = setup
    log.stage(BINDING, 'order-1', RAW)
    with pytest.raises(PermissionError):
        log.discard(BINDING, 'order-1', authorize=lambda row: authorized)
    assert log.get(BINDING, 'order-1')['state'] == 'queued'


def test_owner_revoked_during_discard_rolls_back_row_and_pending(setup):
    log, _, _ = setup
    log.stage(BINDING, 'order-1', RAW)
    results = iter([True, False])
    with pytest.raises(PermissionError):
        log.discard(BINDING, 'order-1', authorize=lambda row: next(results))
    assert log.get(BINDING, 'order-1')['state'] == 'queued'
    with pytest.raises(Conflict):
        log.stage(BINDING, 'order-2', RAW)


@pytest.mark.parametrize('mutation', [{'not_sent': 1}, {'attempt': None}])
def test_malformed_unsent_evidence_cannot_be_discarded(setup, mutation):
    log, store, _ = setup
    row = log.stage(BINDING, 'order-1', RAW)
    key, _ = log._keys(BINDING, 'order-1')
    store.graph.put_doc(key, {**row, **mutation})
    with pytest.raises(Conflict):
        log.discard(BINDING, 'order-1', authorize=lambda row: True)


def test_claim_and_owner_discard_have_one_winner(setup):
    log, _, _ = setup
    log.stage(BINDING, 'order-1', RAW)
    results = []
    def discard():
        try:
            results.append(('discard', log.discard(BINDING, 'order-1', authorize=lambda row: True)['state']))
        except Conflict:
            results.append(('discard', 'denied'))
    def claim():
        _, acquired = log.claim(BINDING, 'order-1')
        results.append(('claim', acquired))
    threads = [threading.Thread(target=discard), threading.Thread(target=claim)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert (('discard', 'discarded') in results) != (('claim', True) in results)
    if ('claim', True) in results:
        row = log.get(BINDING, 'order-1')
        log.interrupt(BINDING, 'order-1', row['attempt'])
        with pytest.raises(Conflict):
            log.discard(BINDING, 'order-1', authorize=lambda row: True)


@pytest.mark.parametrize('interrupted', [False, True])
def test_attempted_or_unknown_event_cannot_be_discarded(setup, interrupted):
    log, _, _ = setup
    log.stage(BINDING, 'order-1', RAW)
    row, _ = log.claim(BINDING, 'order-1')
    if interrupted:
        log.interrupt(BINDING, 'order-1', row['attempt'])
    with pytest.raises(Conflict):
        log.discard(BINDING, 'order-1', authorize=lambda row: True)


@pytest.mark.parametrize('mutation', [{'raw': '{"changed":true}'}, {'event_id': 'other'}])
def test_corrupted_intent_cannot_issue_a_send_claim(setup, mutation):
    log, store, _ = setup
    row = log.stage(BINDING, 'order-1', RAW)
    key, _ = log._keys(BINDING, 'order-1')
    store.graph.put_doc(key, {**row, **mutation})
    with pytest.raises(PermissionError):
        log.claim(BINDING, 'order-1')


def test_uncertain_attempt_evidence_cannot_be_replaced_by_a_fresh_claim(setup):
    log, store, _ = setup
    row = log.stage(BINDING, 'order-1', RAW)
    key, _ = log._keys(BINDING, 'order-1')
    store.graph.put_doc(key, {**row, 'attempt': None})
    actual, acquired = log.claim(BINDING, 'order-1')
    assert acquired is False and actual['state'] == 'interrupted'


@pytest.mark.parametrize('changed', ['missing', 'event', 'binding'])
def test_missing_or_mismatched_pending_index_never_issues_send_authority(setup, changed):
    log, store, _ = setup
    log.stage(BINDING, 'order-1', RAW)
    key, pending = log._keys(BINDING, 'order-1')
    if changed == 'missing':
        store.graph.delete_doc(pending)
    elif changed == 'event':
        store.graph.put_doc(pending, {'key': 'other', 'binding': BINDING})
    else:
        store.graph.put_doc(pending, {'key': key, 'binding': {**BINDING, 'generation': '4' * 32}})
    with pytest.raises(PermissionError):
        log.claim(BINDING, 'order-1')
    assert log.get(BINDING, 'order-1')['state'] == 'queued'


def negative(row):
    from poolhouse.studio.store import digest
    return {'source': row['binding']['source'], 'generation': row['binding']['generation'],
            'event_id': row['event_id'], 'wire_digest': row['wire_digest'],
            'binding_digest': digest(row['binding']), 'attempt': row['attempt'], 'status': 'not_admitted'}


def rejected(setup):
    log, _, state = setup
    log.stage(BINDING, 'order-1', RAW)
    row, acquired = log.claim(BINDING, 'order-1')
    assert acquired
    state['negative'] = negative(row)
    return log.reject(BINDING, 'order-1', row['attempt'])


def test_definitive_rejection_keeps_attempted_tombstone_and_releases_pending(setup):
    log, _, _ = setup
    row = rejected(setup)
    assert row['state'] == 'rejected' and row['not_sent'] is False
    assert log.reject(BINDING, 'order-1', row['attempt']) == row
    assert log.stage(BINDING, 'order-1', RAW) == row
    assert log.claim(BINDING, 'order-1')[1] is False
    assert log.stage(BINDING, 'order-2', RAW)['state'] == 'queued'


@pytest.mark.parametrize('change', [{'source': '0' * 32}, {'generation': '0' * 32},
                                  {'wire_digest': '0' * 64}, {'binding_digest': '0' * 64},
                                  {'event_id': 'other'}, {'attempt': {'instance': 'x', 'token': 'y'}},
                                  {'status': 'rejected'}, {'extra': True}])
def test_rejection_requires_exact_receiver_negative_evidence(setup, change):
    log, _, state = setup
    log.stage(BINDING, 'order-1', RAW)
    row, _ = log.claim(BINDING, 'order-1')
    state['negative'] = {**negative(row), **change}
    with pytest.raises(Conflict):
        log.reject(BINDING, 'order-1', row['attempt'])
    assert log.get(BINDING, 'order-1')['state'] == 'intent'
    with pytest.raises(Conflict):
        log.stage(BINDING, 'order-2', RAW)


def test_positive_receipt_always_wins_over_negative_and_keeps_other_pending(setup):
    log, _, state = setup
    row = rejected(setup)
    log.stage(BINDING, 'order-2', RAW)
    state['proof'] = proof(row)
    assert log.reconcile(BINDING, 'order-1')['state'] == 'done'
    assert log.claim(BINDING, 'order-2')[1] is True


def test_retry_is_explicit_idempotent_and_attempt_nonce_changes_same_second(setup, monkeypatch):
    from poolhouse.studio.store import digest
    monkeypatch.setattr('poolhouse.studio.producer_log.time.time', lambda: 1234)
    log, _, state = setup
    original = rejected(setup)
    key = '4' * 32
    queued = log.retry(BINDING, 'order-1', key)
    assert log.retry(BINDING, 'order-1', key) == queued
    assert queued['raw'] == original['raw'] and queued['binding'] == original['binding']
    assert queued['not_sent'] is False and queued['command_not_attempted'] is True
    assert queued['history'][0]['attempt'] == original['attempt']
    assert log.claim(BINDING, 'order-1')[1] is False
    row, acquired = log.claim(BINDING, 'order-1', retry_key=key)
    assert acquired and row['timestamp'] == original['timestamp']
    assert digest(row['attempt']) != digest(original['attempt'])
    assert log.retry(BINDING, 'order-1', key) == queued
    assert log.claim(BINDING, 'order-1', retry_key=key)[1] is False
    # An old receiver marker cannot reject the new attempt, even within one second.
    with pytest.raises(Conflict):
        log.reject(BINDING, 'order-1', row['attempt'])
    state['negative'] = negative(row)
    log.reject(BINDING, 'order-1', row['attempt'])
    log.retry(BINDING, 'order-1', '5' * 32)
    assert log.claim(BINDING, 'order-1', retry_key=key)[1] is False
    assert log.claim(BINDING, 'order-1', retry_key='5' * 32)[1] is True


def test_unknown_cannot_retry_or_bypass_with_new_id(setup):
    log, _, _ = setup
    log.stage(BINDING, 'order-1', RAW)
    row, _ = log.claim(BINDING, 'order-1')
    log.interrupt(BINDING, 'order-1', row['attempt'])
    with pytest.raises(Conflict):
        log.retry(BINDING, 'order-1', '4' * 32)
    with pytest.raises(Conflict):
        log.reject(BINDING, 'order-1', row['attempt'])
    with pytest.raises(Conflict):
        log.stage(BINDING, 'order-2', RAW)


def test_retry_rechecks_negative_and_current_authority_with_rollback(setup):
    log, _, state = setup
    original = rejected(setup)
    calls = iter([True, False])
    log.current = lambda binding: next(calls)
    with pytest.raises(PermissionError):
        log.retry(BINDING, 'order-1', '4' * 32)
    assert log.get(BINDING, 'order-1') == original
    log.current = lambda binding: True
    negatives = iter([state['negative'], None])
    log.negative = lambda row: next(negatives)
    with pytest.raises(Conflict):
        log.retry(BINDING, 'order-1', '4' * 32)
    assert log.get(BINDING, 'order-1') == original
    log.negative = lambda row: state['negative']
    assert log.retry(BINDING, 'order-1', '4' * 32)['revision'] == 1


def test_retry_refuses_changed_binding_or_intervening_pending(setup):
    log, _, _ = setup
    rejected(setup)
    with pytest.raises(PermissionError):
        log.retry({**BINDING, 'generation': '5' * 32}, 'order-1', '4' * 32)
    log.stage(BINDING, 'order-2', RAW)
    with pytest.raises(Conflict):
        log.retry(BINDING, 'order-1', '4' * 32)


def test_concurrent_retry_commands_have_one_winner(setup):
    log, _, _ = setup
    rejected(setup)
    result = []
    def retry(key):
        try:
            result.append(log.retry(BINDING, 'order-1', key)['command'])
        except Conflict:
            result.append(None)
    threads = [threading.Thread(target=retry, args=(char * 32,)) for char in '45']
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert result.count(None) == 1
    assert sum(log.claim(BINDING, 'order-1', retry_key=char * 32)[1] for char in '45') == 1


def test_stale_reject_cannot_release_new_retry_pending(setup):
    log, _, _ = setup
    original = rejected(setup)
    log.retry(BINDING, 'order-1', '4' * 32)
    with pytest.raises(Conflict):
        log.reject(BINDING, 'order-1', original['attempt'])
    with pytest.raises(Conflict):
        log.stage(BINDING, 'order-2', RAW)


def test_owner_can_cancel_only_unattempted_retry_retaining_history(setup):
    log, _, _ = setup
    original = rejected(setup)
    queued = log.retry(BINDING, 'order-1', '4' * 32)
    discarded = log.discard(BINDING, 'order-1', authorize=lambda row: True)
    assert discarded['state'] == 'discarded' and discarded['not_sent'] is False
    assert discarded['history'] == queued['history']
    assert discarded['history'][0]['attempt'] == original['attempt']
    assert discarded['cancelled_command'] == {'key': '4' * 32, 'revision': 1}
    assert log.discard(BINDING, 'order-1', authorize=lambda row: True) == discarded
    assert log.claim(BINDING, 'order-1', retry_key='4' * 32)[1] is False
    assert log.retry(BINDING, 'order-1', '4' * 32) == queued
    with pytest.raises(Conflict):
        log.retry(BINDING, 'order-1', '5' * 32)
    assert log.stage(BINDING, 'order-2', RAW)['state'] == 'queued'


def test_owner_cancel_retry_revocation_rolls_back_and_claim_race_has_one_winner(setup):
    log, _, _ = setup
    rejected(setup)
    queued = log.retry(BINDING, 'order-1', '4' * 32)
    calls = iter([True, False])
    with pytest.raises(PermissionError):
        log.discard(BINDING, 'order-1', authorize=lambda row: next(calls))
    assert log.get(BINDING, 'order-1') == queued
    result = []
    def discard():
        try:
            result.append(log.discard(BINDING, 'order-1', authorize=lambda row: True)['state'])
        except Conflict:
            result.append('denied')
    def claim():
        result.append(log.claim(BINDING, 'order-1', retry_key='4' * 32)[1])
    threads = [threading.Thread(target=discard), threading.Thread(target=claim)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert ('discarded' in result) != (True in result)


def test_accepted_receipt_beats_429_negative_under_shared_receiver_transaction(setup):
    log, _, state = setup
    log.stage(BINDING, 'order-1', RAW)
    row, _ = log.claim(BINDING, 'order-1')
    state['proof'], state['negative'] = proof(row), negative(row)
    assert log.reject(BINDING, 'order-1', row['attempt'])['state'] == 'done'
    assert log.claim(BINDING, 'order-1')[1] is False


@pytest.mark.parametrize('bad', [None, True, {}, {'status': 'not_admitted'}])
def test_unknown_or_truthy_negative_never_authorizes_rejection(setup, bad):
    log, _, state = setup
    log.stage(BINDING, 'order-1', RAW)
    row, _ = log.claim(BINDING, 'order-1')
    state['negative'] = bad
    with pytest.raises(Conflict):
        log.reject(BINDING, 'order-1', row['attempt'])


def test_restart_retains_exact_retry_command_and_never_reclaims_initial(setup):
    log, store, _ = setup
    rejected(setup)
    queued = log.retry(BINDING, 'order-1', '4' * 32)
    restarted = ProducerLog(store, current=log.current, receipt=log.receipt, negative=log.negative)
    assert restarted.retry(BINDING, 'order-1', '4' * 32) == queued
    assert restarted.claim(BINDING, 'order-1')[1] is False
    row, acquired = restarted.claim(BINDING, 'order-1', retry_key='4' * 32)
    assert acquired
    assert restarted.interrupt(BINDING, 'order-1', queued['history'][0]['attempt']) == row
    with pytest.raises(Conflict):
        restarted.discard(BINDING, 'order-1', authorize=lambda row: True)


def test_old_retry_snapshot_after_done_is_not_current_status_or_send_authority(setup):
    log, _, state = setup
    rejected(setup)
    queued = log.retry(BINDING, 'order-1', '4' * 32)
    row, acquired = log.claim(BINDING, 'order-1', retry_key='4' * 32)
    assert acquired
    state['proof'] = proof(row)
    assert log.reconcile(BINDING, 'order-1')['state'] == 'done'
    assert log.retry(BINDING, 'order-1', '4' * 32) == queued
    assert log.get(BINDING, 'order-1')['state'] == 'done'
    assert log.claim(BINDING, 'order-1', retry_key='4' * 32)[1] is False

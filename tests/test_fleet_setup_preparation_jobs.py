"""Preparation commands retain durable identity without creating another worker."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from poolhouse.fleet import setup_jobs


def request(key='a' * 32):
    return {'idempotency_key': key, 'binding': {'scope': 'one', 'device': 'device'},
            'selection': {'model_id': 'model', 'download': False}}


def finished(jobs, ident):
    until = time.monotonic() + 5
    while time.monotonic() < until:
        row = jobs.get(ident)
        if row['state'] not in setup_jobs.ACTIVE:
            return row
        time.sleep(.01)
    raise AssertionError(jobs.get(ident))


def test_fixed_probe_intents_are_durable_once_and_receipts_are_immutable(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)

    def operation(progress):
        progress.stage('probe')
        for phase in setup_jobs.PROBES:
            first = progress.probe(phase, 'b' * 64)
            assert first['fresh'] is True and first['status'] == 'intent'
            stored = jobs.get(progress.row['id'])['probes'][phase]
            assert stored == {key: value for key, value in first.items() if key != 'fresh'}
            assert progress.probe(phase, 'b' * 64) == {**first, 'fresh': False}
            with pytest.raises(ValueError, match='different request'):
                progress.probe(phase, 'c' * 64)
            receipt = progress.probe(phase, 'b' * 64, response_digest='d' * 64, accepted=True)
            assert receipt['fresh'] is False and receipt['status'] == 'accepted'
            assert progress.probe(phase, 'b' * 64, response_digest='d' * 64, accepted=True) == receipt
            with pytest.raises(ValueError, match='different receipt'):
                progress.probe(phase, 'b' * 64, response_digest='e' * 64, accepted=True)
        return {}

    row = jobs.prepare(request(), operation, current=lambda: True)
    result = finished(jobs, row['id'])
    assert result['state'] == 'done'
    assert set(result['probes']) == set(setup_jobs.PROBES)
    assert len({receipt['id'] for receipt in result['probes'].values()}) == 3


def test_uncertain_probe_intent_never_replays_after_reopen(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)
    effects = []

    def operation(progress):
        progress.stage('probe')
        assert progress.probe('chat', 'b' * 64)['fresh'] is True
        effects.append('request with lost response')
        raise OSError('isolated unknown response')

    row = jobs.prepare(request(), operation, current=lambda: True)
    result = finished(jobs, row['id'])
    assert result['state'] == 'interrupted' and result['probes']['chat']['status'] == 'intent'
    reopened = setup_jobs.Jobs(tmp_path)
    assert reopened.prepare(request(), operation, current=lambda: True)['id'] == row['id']
    assert effects == ['request with lost response']


def test_probe_phase_and_receipt_require_fixed_successful_predecessor(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)

    def operation(progress):
        with pytest.raises(ValueError, match='intent'):
            progress.probe('chat', 'b' * 64)
        progress.stage('probe')
        for phase in ('tool-call', 'tool-result'):
            with pytest.raises(ValueError, match='previous'):
                progress.probe(phase, 'b' * 64)
        with pytest.raises(ValueError, match='intent'):
            progress.probe('chat', 'b' * 64, response_digest='c' * 64, accepted=True)
        progress.probe('chat', 'b' * 64)
        progress.probe('chat', 'b' * 64, response_digest='c' * 64, accepted=False)
        with pytest.raises(ValueError, match='previous'):
            progress.probe('tool-call', 'b' * 64)
        for phase, fingerprint in (('unknown', 'b' * 64), ('chat', True), ('chat', 'x' * 64)):
            with pytest.raises(ValueError):
                progress.probe(phase, fingerprint)
        with pytest.raises(ValueError):
            progress.probe('chat', 'b' * 64, response_digest='c' * 64, accepted=1)
        return {}

    row = jobs.prepare(request(), operation, current=lambda: True)
    assert finished(jobs, row['id'])['state'] == 'done'


def test_probe_current_revocation_after_save_rolls_back_before_fresh_claim(tmp_path, monkeypatch):
    jobs = setup_jobs.Jobs(tmp_path)
    active, effects = [True], []

    def operation(progress):
        progress.stage('probe')
        save = jobs._save

        def revoke(graph, row):
            save(graph, row)
            if row.get('probes'):
                active[0] = False

        monkeypatch.setattr(jobs, '_save', revoke)
        progress.probe('chat', 'b' * 64)
        effects.append('forbidden request')
        return {}

    row = jobs.prepare(request(), operation, current=lambda: active[0])
    result = finished(jobs, row['id'])
    assert result['state'] == 'interrupted' and 'probes' not in result
    assert effects == []


def test_cancelled_preparation_cannot_claim_a_probe(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)
    entered, release = threading.Event(), threading.Event()
    effects = []

    def operation(progress):
        progress.stage('probe')
        entered.set()
        assert release.wait(5)
        progress.probe('chat', 'b' * 64)
        effects.append('forbidden request')
        return {}

    row = jobs.prepare(request(), operation, current=lambda: True)
    try:
        assert entered.wait(5)
        jobs.cancel_preparation(row['id'], binding=request()['binding'], current=lambda: True)
        release.set()
        result = finished(jobs, row['id'])
        assert result['state'] == 'cancelled' and 'probes' not in result
        assert effects == []
    finally:
        release.set()


def test_concurrent_repeat_and_terminal_reopen_attach_one_operation(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)
    enter, release = threading.Event(), threading.Event()
    calls = []

    def operation(progress):
        calls.append(1)
        progress.stage('verify')
        enter.set()
        assert release.wait(5)
        progress.stage('ready')
        return {'chat_ready': True, 'tools_ready': False}

    try:
        with ThreadPoolExecutor(4) as executor:
            rows = list(executor.map(lambda _: jobs.prepare(request(), operation, current=lambda: True), range(4)))
        assert len({row['id'] for row in rows}) == 1
        assert enter.wait(5)
        assert jobs.get(rows[0]['id'])['stage'] == 'verify'
        release.set()
        result = finished(jobs, rows[0]['id'])
        assert result['state'] == 'done'
        assert result['result'] == {'chat_ready': True, 'tools_ready': False}
        assert jobs.prepare(request(), operation, current=lambda: True)['id'] == result['id']
        assert calls == [1]
    finally:
        release.set()


def test_changed_command_conflicts_and_inputs_are_copied(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)
    body = request()
    first = jobs.prepare(body, lambda progress: {}, current=lambda: True)
    body['selection']['download'] = True
    with pytest.raises(ValueError, match='different selection'):
        jobs.prepare(body, lambda progress: pytest.fail('must not execute'), current=lambda: True)
    assert finished(jobs, first['id'])['request']['selection']['download'] is False


def test_cancel_running_preserves_intent_and_stops_next_effect(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)
    entered, release = threading.Event(), threading.Event()
    effects = []

    def operation(progress):
        progress.stage('serve')
        effects.append('current indivisible effect')
        entered.set()
        assert release.wait(5)
        progress.stage('probe')
        effects.append('forbidden future effect')
        return {}

    row = jobs.prepare(request(), operation, current=lambda: True)
    try:
        assert entered.wait(5)
        for _ in range(2):
            receipt = jobs.cancel_preparation(row['id'], binding=request()['binding'], current=lambda: True)
            assert receipt['cancel_requested'] is True
            assert receipt['state'] == 'installing'
        release.set()
        final = finished(jobs, row['id'])
        assert final['state'] == 'cancelled'
        assert final['stage'] == 'serve'
        assert effects == ['current indivisible effect']
        assert jobs.prepare(request(), operation, current=lambda: True)['id'] == row['id']
    finally:
        release.set()


def test_current_revocation_interrupts_without_raw_error_or_repeat(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)
    current = [True]

    def operation(progress):
        progress.stage('transfer', done=2, total=8)
        current[0] = False
        progress.check()
        pytest.fail('must stop')

    row = jobs.prepare(request(), operation, current=lambda: current[0])
    final = finished(jobs, row['id'])
    assert final['state'] == 'interrupted'
    assert final['progress'] == {'done': 2, 'total': 8}
    current[0] = True
    assert jobs.prepare(request(), lambda p: pytest.fail('must not repeat'), current=lambda: True)['id'] == row['id']


def test_unknown_effect_after_intent_and_restart_never_replays(tmp_path, monkeypatch):
    jobs = setup_jobs.Jobs(tmp_path)
    row = jobs.prepare(request(), lambda progress: {}, current=lambda: True)
    finished(jobs, row['id'])
    with jobs._store() as graph:
        saved = row.copy()
        saved.update(state='installing', stage='probe', pid=987654, born=0)
        jobs._save(graph, saved)
    monkeypatch.setattr(setup_jobs, 'pid_exists', lambda pid: False)
    reopened = setup_jobs.Jobs(tmp_path)
    assert reopened.get(row['id'])['state'] == 'interrupted'
    assert reopened.prepare(request(), lambda p: pytest.fail('must not repeat'), current=lambda: True)['id'] == row['id']
    assert reopened._pending == []


def test_cancel_authorization_rolls_back_and_binding_is_not_adopted(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def operation(progress):
        entered.set()
        assert release.wait(5)
        return {}

    row = jobs.prepare(request(), operation, current=lambda: True)
    try:
        assert entered.wait(5)
        calls = iter([True, False])
        with pytest.raises(PermissionError):
            jobs.cancel_preparation(row['id'], binding=request()['binding'], current=lambda: next(calls))
        assert jobs.get(row['id'])['cancel_requested'] is False
        with pytest.raises(KeyError):
            jobs.cancel_preparation(row['id'], binding={'scope': 'other'}, current=lambda: True)
    finally:
        release.set()
        finished(jobs, row['id'])


@pytest.mark.parametrize('field,value', [('idempotency_key', True), ('idempotency_key', 'unknown'),
                                        ('binding', {}), ('selection', None)])
def test_invalid_command_never_queues(tmp_path, field, value):
    jobs = setup_jobs.Jobs(tmp_path)
    body = request()
    body[field] = value
    with pytest.raises(ValueError):
        jobs.prepare(body, lambda progress: {}, current=lambda: True)
    assert jobs.all() == []


@pytest.mark.parametrize('value', [False, 1, None])
def test_current_requires_exact_true(tmp_path, value):
    jobs = setup_jobs.Jobs(tmp_path)
    with pytest.raises(PermissionError):
        jobs.prepare(request(), lambda progress: {}, current=lambda: value)
    assert jobs.all() == []


@pytest.mark.parametrize('change', ['cancel', 'revoke'])
def test_completion_reloads_cancel_and_current_before_publishing_ready(tmp_path, monkeypatch, change):
    jobs = setup_jobs.Jobs(tmp_path)
    allowed = [True]
    original = jobs._finish_preparation

    def finish(row, result, current):
        if change == 'cancel':
            jobs.cancel_preparation(row['id'], binding=request()['binding'], current=lambda: True)
        else:
            allowed[0] = False
        original(row, result, current)

    monkeypatch.setattr(jobs, '_finish_preparation', finish)
    row = jobs.prepare(request(), lambda progress: {'chat_ready': True}, current=lambda: allowed[0])
    final = finished(jobs, row['id'])
    assert final['state'] == ('cancelled' if change == 'cancel' else 'interrupted')
    assert final['result'] == {}


def test_completion_revocation_after_save_rolls_back_ready(tmp_path):
    jobs = setup_jobs.Jobs(tmp_path)
    entered, release = threading.Event(), threading.Event()
    allowed = [True]

    def operation(progress):
        entered.set()
        assert release.wait(5)
        return {'chat_ready': True}

    row = jobs.prepare(request(), operation, current=lambda: allowed[0])
    assert entered.wait(5)
    original = jobs._save

    def save(graph, value):
        original(graph, value)
        if value.get('state') == 'done':
            allowed[0] = False

    jobs._save = save
    release.set()
    final = finished(jobs, row['id'])
    assert final['state'] == 'interrupted'
    assert final['result'] == {}


def test_failed_enqueue_transaction_never_schedules_or_executes(tmp_path, monkeypatch):
    jobs = setup_jobs.Jobs(tmp_path)
    original_store = jobs._store
    called = threading.Event()

    def store():
        graph = original_store()
        transaction = graph.transaction
        depth = [0]

        @contextmanager
        def failure():
            outer = depth[0] == 0
            depth[0] += 1
            try:
                with transaction():
                    yield
                    if outer:
                        raise RuntimeError('commit refused')
            finally:
                depth[0] -= 1

        graph.transaction = failure
        return graph

    monkeypatch.setattr(jobs, '_store', store)
    with pytest.raises(RuntimeError, match='commit refused'):
        jobs.prepare(request(), lambda progress: called.set(), current=lambda: True)
    monkeypatch.setattr(jobs, '_store', original_store)
    assert jobs._pending == [] and jobs._worker is None
    assert jobs.all() == [] and not called.is_set()


def test_owned_worker_start_failure_retains_interrupted_command_without_pending(tmp_path, monkeypatch):
    jobs = setup_jobs.Jobs(tmp_path)

    def failure(thread):
        raise RuntimeError('thread start refused')

    monkeypatch.setattr(threading.Thread, 'start', failure)
    with pytest.raises(RuntimeError, match='thread start refused'):
        jobs.prepare(request(), lambda progress: pytest.fail('must not execute'), current=lambda: True)
    assert jobs._pending == [] and jobs._worker is None
    row = jobs.all()[0]
    assert row['state'] == 'interrupted'
    assert jobs.prepare(request(), lambda progress: pytest.fail('must not repeat'), current=lambda: True)['id'] == row['id']

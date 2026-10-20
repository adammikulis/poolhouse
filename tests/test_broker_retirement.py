"""Idle replacement seals every broker admission before examining native work."""
import os
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from poolhouse import maintenance
from poolhouse.serve import broker_holds, broker_wire
from poolhouse.serve.broker import Broker
from poolhouse.serve.broker_lifecycle import admitted, retire
from poolhouse.serve.decision_residents import DecisionResidents


@pytest.fixture
def broker(tmp_path):
    table = SimpleNamespace(listing=lambda: {"leases": []})
    return Broker(manager=SimpleNamespace(table=table, state_file=tmp_path / "servers.json"), scan=lambda: [])


@pytest.mark.parametrize("field", ["servers", "queue", "claims", "claim_waiters", "cores"])
def test_each_python_work_owner_prevents_retirement(broker, field):
    setattr(broker, field, ["active"])
    assert not retire(broker) and not broker._retiring
    setattr(broker, field, {})
    assert retire(broker)


@pytest.mark.parametrize("state", ["held", "queued"])
def test_native_work_cannot_be_ignored_or_stopped(broker, state):
    rows = [{"id": "external", "state": state}]
    broker.table.listing = lambda: {"leases": rows}
    assert not retire(broker) and rows == [{"id": "external", "state": state}]
    assert not broker._retiring
    rows.clear()
    assert retire(broker)


@pytest.mark.parametrize('kind', ['branch', 'worktree', 'area', 'install'])
@pytest.mark.parametrize('state', ['held', 'queued'])
def test_coordination_only_native_claims_do_not_block_idle_retirement(broker, kind, state):
    rows = [{'id': 'coordination', 'state': state, 'pid': 0, 'holder': 'other-board',
             'resources': [{'type': 'claim', 'kind': kind, 'name': 'owned-scope'}]}]
    broker.table.listing = lambda: {'leases': rows}
    assert retire(broker) and broker._retiring
    assert rows[0]['resources'] == [{'type': 'claim', 'kind': kind, 'name': 'owned-scope'}]


@pytest.mark.parametrize('resource', [
    {'type': 'gpu'}, {'type': 'cpu_slots', 'count': 1}, {'type': 'memory_mb', 'mb': 1},
    {'type': 'model_slot', 'model': 'owned'},
    {'type': 'claim', 'kind': 'server', 'name': 'line:owned'},
    {'type': 'claim', 'kind': 'port', 'name': '1234'},
    {'type': 'claim', 'kind': 'task', 'name': 'owned'},
    {'type': 'claim', 'kind': 'future', 'name': 'owned'},
    {'type': 'future'}, None,
])
@pytest.mark.parametrize('state', ['held', 'queued'])
def test_coordination_mixed_with_any_compute_or_unknown_resource_stays_busy(broker, resource, state):
    coordination = {'type': 'claim', 'kind': 'branch', 'name': 'owned'}
    rows = [{'id': 'mixed', 'state': state, 'resources': [coordination, resource]}]
    broker.table.listing = lambda: {'leases': rows}
    assert not retire(broker) and not broker._retiring
    assert rows[0]['resources'] == [coordination, resource]


@pytest.mark.parametrize('row', [
    None, [], {}, {'id': 'owned', 'state': 'held', 'resources': []},
    {'id': 'owned', 'state': 'queued', 'resources': []},
    {'id': 'owned', 'state': 'held', 'resources': {}},
    {'state': 'held', 'resources': [{'type': 'claim', 'kind': 'branch', 'name': 'owned'}]},
    {'id': 'owned', 'state': 'future', 'resources': [{'type': 'claim', 'kind': 'branch', 'name': 'owned'}]},
    {'id': 'owned', 'state': [], 'resources': [{'type': 'claim', 'kind': 'branch', 'name': 'owned'}]},
    {'id': 'owned', 'state': 'held', 'resources': [{'type': 'claim', 'kind': 'branch', 'name': ''}]},
    {'id': 'owned', 'state': 'held', 'resources': [{'type': 'claim', 'kind': 'branch', 'name': '  '}]},
    {'id': 'owned', 'state': 'held', 'resources': [{'type': 'claim', 'kind': 'branch', 'name': 1}]},
    {'id': 'owned', 'state': 'held', 'resources': [{'type': 'claim', 'kind': 'branch', 'name': 'owned', 'future': True}]},
])
def test_malformed_or_redacted_native_rows_still_block_retirement(broker, row):
    broker.table.listing = lambda: {'leases': [row]}
    assert not retire(broker) and not broker._retiring


def test_resident_without_a_proven_drain_blocks_retirement(broker):
    hosts = SimpleNamespace(residents={"owned": {"pid": 123}})
    hosts.drain_idle = lambda: not hosts.residents
    broker._decision_hosts = hosts
    assert not retire(broker)
    broker._decision_hosts.residents.clear()
    assert retire(broker)


@pytest.fixture
def cached(broker):
    held = {'memory': {'id': 'memory', 'state': 'held',
                       'resources': [{'type': 'memory_mb', 'mb': 1}]}}
    busy, events = [False], []
    def acquire(resources, terms):
        assert resources == [{'type': 'gpu'}] and terms.pid == os.getpid() and not terms.wait
        events.append('gpu admission')
        if busy[0]:
            return {'state': 'busy'}
        held['gpu'] = {'id': 'gpu', 'state': 'held', 'resources': resources}
        return held['gpu']
    def release(identity):
        assert identity != 'memory' or not process.alive()
        events.append('release ' + identity)
        del held[identity]
    broker.table.acquire, broker.table.release = acquire, release
    broker.table.listing = lambda: {'leases': list(held.values())}
    process = SimpleNamespace(pid=os.getpid(), ended=False)
    process.alive = lambda: not process.ended
    def stop():
        assert not broker._retiring and set(held) == {'memory', 'gpu'}
        assert broker.claims['gpu-training']['pid'] == process.pid
        events.append('stop')
        process.ended = True
    process.close = stop
    registry = DecisionResidents(broker)
    registry.residents['owned'] = {'process': process, 'lease': 'memory', 'bytes': 1, 'used': time.monotonic()}
    broker._decision_hosts = registry
    try:
        yield SimpleNamespace(broker=broker, registry=registry, held=held, busy=busy,
                              process=process, events=events, stop=stop)
    finally:
        process.close = stop
        registry.close()


def test_idle_cache_drains_under_real_broker_gpu_admission_before_seal(cached):
    assert retire(cached.broker)
    assert cached.events == ['gpu admission', 'stop', 'release gpu', 'release memory']
    assert not cached.registry.residents and not cached.held and cached.broker._retiring


def test_busy_native_gpu_preserves_idle_host_and_memory(cached):
    cached.busy[0] = True
    assert not retire(cached.broker)
    assert cached.registry.residents and not cached.process.ended
    assert set(cached.held) == {'memory'} and not cached.broker._retiring
    cached.busy[0] = False


def test_registry_in_use_refuses_drain_without_lock_inversion(cached):
    entered, finish = threading.Event(), threading.Event()
    def using():
        with cached.registry.lock:
            entered.set()
            assert finish.wait(2)
    thread = threading.Thread(target=using)
    thread.start()
    try:
        assert entered.wait(2)
        assert not retire(cached.broker) and thread.is_alive()
        assert not cached.events and set(cached.held) == {'memory'}
    finally:
        finish.set()
        thread.join(2)


@pytest.mark.parametrize('raises', [False, True])
def test_uncertain_idle_host_stop_keeps_compute_memory_and_tracking(cached, raises):
    def uncertain():
        assert set(cached.held) == {'memory', 'gpu'}
        if raises:
            raise InterruptedError('Owned host stop is uncertain')
    cached.process.close = uncertain
    if raises:
        with pytest.raises(InterruptedError, match='uncertain'):
            retire(cached.broker)
    else:
        assert not retire(cached.broker)
    assert not cached.broker._retiring and cached.registry.residents
    assert set(cached.held) == {'memory', 'gpu'} and cached.broker.claims


def test_idle_drain_uses_accounting_reader_under_update_intent(cached):
    finished, errors = threading.Event(), []
    def drain():
        try:
            assert retire(cached.broker)
        except (AssertionError, OSError, RuntimeError, ValueError) as error:
            errors.append(error)
        finally:
            finished.set()
    thread = threading.Thread(target=drain)
    try:
        with maintenance.intent():
            thread.start()
            assert finished.wait(2), 'Drain must run before the updater takes content exclusively'
    finally:
        thread.join(2)
    assert not errors and not thread.is_alive()


def test_new_request_cannot_enter_while_owned_cache_is_stopping(cached):
    stopping, finish, attempting, entered = (threading.Event() for _ in range(4))
    class ObservedCondition(threading.Condition):
        def __enter__(self):
            if threading.current_thread().name == 'racing-admission':
                attempting.set()
            return super().__enter__()
    cached.broker._cond = ObservedCondition(threading.RLock())
    def close():
        stopping.set()
        assert finish.wait(2)
        cached.stop()
    cached.process.close = close
    @admitted
    def request(_broker):
        entered.set()
    retired, denied = [], []
    def new_request():
        try:
            request(cached.broker)
        except RuntimeError as error:
            denied.append(str(error))
    cleanup = threading.Thread(target=lambda: retired.append(retire(cached.broker)))
    racing = threading.Thread(target=new_request, name='racing-admission')
    try:
        cleanup.start()
        assert stopping.wait(2)
        racing.start()
        assert attempting.wait(2)
        assert not entered.wait(.05)
    finally:
        finish.set()
        cleanup.join(2)
        if racing.ident is not None:
            racing.join(2)
    assert retired == [True] and denied and 'retiring' in denied[0]
    assert not entered.is_set() and not cleanup.is_alive() and not racing.is_alive()


def test_final_native_unknown_work_keeps_broker_open_after_cache_drains(cached):
    cached.broker.table.listing = lambda: {'leases': [{'id': 'external', 'state': 'queued', 'resources': []}]}
    assert not retire(cached.broker) and not cached.broker._retiring
    assert not cached.registry.residents and not cached.held


def test_active_request_never_drains_its_resident(cached):
    cached.broker._active_calls = 1
    try:
        assert not retire(cached.broker)
        assert not cached.events and not cached.process.ended
    finally:
        cached.broker._active_calls = 0


def test_unavailable_or_malformed_native_listing_restores_open_state(broker):
    def unavailable():
        raise OSError("table unavailable")
    broker.table.listing = unavailable
    with pytest.raises(OSError):
        retire(broker)
    assert not broker._retiring
    broker.table.listing = lambda: {}
    with pytest.raises(RuntimeError, match="active leases"):
        retire(broker)
    assert not broker._retiring
    broker.table.listing = lambda: {"leases": []}
    assert retire(broker)


def test_active_background_and_nested_calls_refuse_retire_without_deadlock(broker):
    entered, finish = threading.Event(), threading.Event()
    @admitted
    def inner(current):
        assert current._active_calls == 2
        entered.set()
        assert finish.wait(2)
    @admitted
    def outer(current):
        inner(current)
    worker = threading.Thread(target=outer, args=(broker,))
    worker.start()
    try:
        assert entered.wait(2)
        assert not retire(broker)
        assert not broker._retiring
    finally:
        finish.set()
        worker.join(2)
    assert not worker.is_alive() and broker._active_calls == 0
    assert retire(broker)
    with pytest.raises(RuntimeError, match="retiring"):
        outer(broker)


def test_seal_precedes_native_empty_check_and_cannot_be_reopened(broker):
    @admitted
    def racing_start(_broker):
        pytest.fail("work admitted after seal")
    def listing():
        with pytest.raises(RuntimeError, match="retiring"):
            racing_start(broker)
        return {"leases": []}
    broker.table.listing = listing
    assert retire(broker) and retire(broker)
    assert broker._retiring


def test_all_actual_broker_entry_points_refuse_after_seal(broker):
    assert retire(broker)
    calls = [lambda: broker.start(None), lambda: broker.lease(None, timeout=0),
             lambda: broker.decision_once(None, None, None, None, None),
             lambda: broker.claim("gpu-training", 123, {}), lambda: broker.take_cores(123, 1),
             lambda: broker.adopt(), lambda: broker.detach(None), lambda: broker.escalate(None, None)]
    calls.append(lambda: broker.keep_leases())
    for call in calls:
        with pytest.raises(RuntimeError, match="retiring"):
            call()
    assert broker._active_calls == 0


def test_background_missing_lease_reacquisition_is_inside_admission(broker, monkeypatch):
    entered, finish = threading.Event(), threading.Event()
    monkeypatch.setattr(broker_holds, "recorded_servers", lambda _path: {7443: {"pid": 123}})
    broker.alive = lambda _pid: True
    def acquire(*_args):
        entered.set()
        assert finish.wait(2)
    monkeypatch.setattr(broker_holds.admission, "keep", acquire)
    worker = threading.Thread(target=broker.keep_leases)
    worker.start()
    try:
        assert entered.wait(2)
        assert not retire(broker)
    finally:
        finish.set()
        worker.join(2)
    assert not worker.is_alive() and retire(broker)
    with pytest.raises(RuntimeError, match="retiring"):
        broker.keep_leases()


def test_wire_retirement_requires_connected_owner_and_never_closes_busy_broker(broker, monkeypatch):
    monkeypatch.setattr(broker_wire.broker_runtime, "snapshot", lambda: {})
    rows = [{'id': 'coordination', 'state': 'held', 'resources': [
        {'type': 'claim', 'kind': 'worktree', 'name': 'owned-scope'}]}]
    broker.table.listing = lambda: {'leases': rows}
    read = maintenance.read
    @contextmanager
    def accounting_read(*, existing=False):
        assert existing and not broker._cond._is_owned()
        with read(existing=existing):
            yield
    monkeypatch.setattr(maintenance, 'read', accounting_read)
    server = broker_wire._Server(broker)
    launched = []
    monkeypatch.setattr(broker_wire.threading, "Thread", lambda **kwargs:
                        SimpleNamespace(start=lambda: launched.append(kwargs["target"])))
    try:
        request = {"token": server.token, "op": "idle-retire"}
        with pytest.raises(PermissionError):
            server.answer(request, connected=lambda: False)
        broker.claims["busy"] = {"pid": 123}
        assert server.answer(request, connected=lambda: True) == {"ok": True, "retiring": False}
        assert not launched and not server.closing.is_set()
        broker.claims.clear()
        assert server.answer(request, connected=lambda: True) == {"ok": True, "retiring": True}
        assert len(launched) == 1 and server.closing.is_set()
        assert rows[0]['resources'][0]['kind'] == 'worktree'
        with pytest.raises(RuntimeError, match="retiring"):
            server.answer({"token": server.token, "op": "start", "spec": {}}, connected=lambda: True)
    finally:
        server.server_close()

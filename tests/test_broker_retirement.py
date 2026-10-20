"""Idle replacement seals every broker admission before examining native work."""
import threading
from types import SimpleNamespace

import pytest

from poolhouse.serve import broker_holds, broker_wire
from poolhouse.serve.broker import Broker
from poolhouse.serve.broker_lifecycle import admitted, retire


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


def test_resident_even_without_python_compute_claim_blocks_retirement(broker):
    broker._decision_hosts = SimpleNamespace(residents={"owned": {"pid": 123}})
    assert not retire(broker)
    broker._decision_hosts.residents.clear()
    assert retire(broker)


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

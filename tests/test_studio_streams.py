"""Transactional event ordering and bounded reconnect history without model compute."""

import pytest

from poolhouse.studio.store import Conflict, StudioStore
from poolhouse.studio.stream_store import StreamStore

P = {'id': 'owner', 'scope': 'scope'}
S = {'id': 'studio-grant:grant', 'kind': 'studio_subscription', 'grant_id': 'grant', 'scope': 'scope'}


@pytest.fixture
def setup(tmp_path):
    base = StudioStore(tmp_path / 'studio.ladybug')
    store = StreamStore(base)
    source = store.create('source', P, {'kind': 'examples', 'label': 'Examples'}, 'source')
    sub = store.create('subscription', P, {'source_id': source['id'], 'input_node': 'record', 'output_nodes': ['record']}, 'sub')
    sub.update(state='active', grant='grant', service_principal=S)
    store.put_subscription(sub)
    yield store, sub
    base.close()


def incoming(value=1):
    return {'id': 'source-event', 'generation': 'generation', 'payload': {'value': value}, 'cursor': {'offset': 10}}


def test_duplicate_attaches_and_mutated_duplicate_conflicts(setup):
    store, sub = setup
    first = store.admit(sub, incoming(), S, lambda *args: True)
    assert store.admit(sub, incoming(), S, lambda *args: True) == first
    with pytest.raises(Conflict):
        store.admit(sub, incoming(2), S, lambda *args: True)
    assert store.get('subscription', sub['id'], 'scope')['next_sequence'] == 2


def test_backpressure_never_advances_source_cursor(setup):
    store, sub = setup
    store.admit(sub, incoming(), S, lambda *args: True)
    with pytest.raises(BlockingIOError):
        store.admit(store.get('subscription', sub['id'], 'scope'), {**incoming(), 'id': 'next', 'cursor': {'offset': 20}}, S, lambda *args: True)
    assert store.get('subscription', sub['id'], 'scope')['cursor'] == {'offset': 10}


def test_revoke_before_commit_acknowledges_nothing(setup):
    store, sub = setup
    with pytest.raises(PermissionError):
        store.admit(sub, incoming(), S, lambda *args: False)
    current = store.get('subscription', sub['id'], 'scope')
    assert current['next_sequence'] == 1 and current['cursor'] is None


def test_failure_rolls_back_inbox_cursor_and_sequence(setup, monkeypatch):
    store, sub = setup
    original = store.put_subscription
    monkeypatch.setattr(store, 'put_subscription', lambda row: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError):
        store.admit(sub, incoming(), S, lambda *args: True)
    monkeypatch.setattr(store, 'put_subscription', original)
    assert store.get('subscription', sub['id'], 'scope')['next_sequence'] == 1
    assert store.admit(sub, incoming(), S, lambda *args: True)['sequence'] == 1


def test_quarantine_advances_complete_bad_record_without_dispatch(setup):
    store, sub = setup
    event = store.admit(sub, {'id': 'bad', 'generation': 'g', 'error': 'invalid JSON record', 'cursor': {'offset': 5}}, S, lambda *args: True)
    current = store.get('subscription', sub['id'], 'scope')
    assert event['state'] == 'quarantined' and 'envelope' not in event
    assert current['inflight'] is None and current['cursor'] == {'offset': 5}


def test_tail_reads_direct_last_sequence_only(setup, monkeypatch):
    store, sub = setup
    for number in range(8):
        store.admit(sub, {**incoming(), 'id': str(number)}, S, lambda *args: True)
        current = store.get('subscription', sub['id'], 'scope')
        current['inflight'] = None
        store.put_subscription(current)
        sub = current
    calls = []
    original = store.graph.get_doc
    def get(key):
        calls.append(key)
        return original(key)
    monkeypatch.setattr(store.graph, 'get_doc', get)
    page = store.page(sub, tail=True, limit=2)
    assert [row['sequence'] for row in page['events']] == [7, 8]
    assert len([key for key in calls if key.startswith('studio.event-sequence:')]) == 2
    assert page['next_cursor'] == 8


def test_actual_finite_event_survives_submit_ack_loss_and_delivers_once(tmp_path):
    from types import SimpleNamespace

    from poolhouse.fleet.studio_authority import StudioAuthority
    from poolhouse.studio.engine import WorkflowEngine
    from poolhouse.studio.streams import Streams

    store = StudioStore(tmp_path / 'studio.ladybug')
    authority = StudioAuthority(tmp_path / 'authority', current=lambda: {'pool': 'a' * 64, 'device': 'b' * 64,
                               'scope': 'scope', 'active': True}, owner=lambda principal: 'launch-ticket',
                               resources=lambda *args: True)
    calls = []
    class Adapters:
        def immediate(self, node, inputs, context):
            calls.append(inputs['record'])
            return {'record': inputs['record']}
    runner = SimpleNamespace(files_root=tmp_path / 'files', jobs={})
    engine = WorkflowEngine(store, runner, Adapters(), lambda p, n, i: authority.authorize(p, n, i) if p.get('kind') else True)
    engine.close()
    sources = SimpleNamespace(close=lambda: None, poll=lambda *args, **kwargs: None, bind=lambda *args: None)
    service = Streams(engine, authority, sources=sources, resources=lambda *args: [])
    try:
        source = service.store.create('source', P, {'kind': 'examples'}, 'source')
        workflow = store.save_workflow('scope', 'Records', {'nodes': [{'id': 'record', 'type': 'stream.record'}], 'edges': []})
        sub = service.create(P, {'workflow': workflow, 'source_id': source['id'], 'input_node': 'record',
                                'output_nodes': ['record'], 'destinations': [{'kind': 'live'}]}, 'create')
        request = {'revision': 1, 'idempotency_key': 'start'}
        sub = service.activate(P, sub['id'], request)
        assert service.activate(P, sub['id'], request) == sub
        admitted = service.ingest(sub, incoming())
        # Crash after engine transaction but before service stores returned run ID.
        original = engine.submit_event(sub['service_principal'], sub['workflow'], admitted['envelope'])
        service.advance(sub)
        persisted = store.get('stream-event', admitted['id'], 'scope')
        assert persisted['run_id'] == original['id']
        engine.advance(store.get('run', original['id'], 'scope'))
        service.advance(sub)
        page = service.store.page(sub)
        assert page['events'][0]['state'] == 'done'
        assert page['events'][0]['result']['outputs'][0]['value'] == {'value': 1}
        assert calls == [{'value': 1}]
        assert service.store.get('subscription', sub['id'], 'scope')['inflight'] is None
    finally:
        service.close()
        authority.close()
        store.close()


def test_stale_poll_cannot_overwrite_new_cursor_or_paused_state(setup):
    store, sub = setup
    fresh = store.get('subscription', sub['id'], 'scope')
    fresh.update(cursor={'offset': 99}, state='paused', revision=2)
    store.put_subscription(fresh)
    with pytest.raises(Conflict):
        store.admit(sub, incoming(), S, lambda *args: True)
    assert store.get('subscription', sub['id'], 'scope')['cursor'] == {'offset': 99}


def test_corrupt_active_pointer_does_not_stop_healthy_subscription(setup):
    store, sub = setup
    store.graph.query("MERGE (a:StudioSubscriptionActive {id:'missing'}) SET a.scope='scope'")
    assert [row['id'] for row in store.active()] == [sub['id']]
    assert store.graph.get_doc('studio.stream-index-error:missing')

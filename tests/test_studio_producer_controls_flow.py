"""Real same-Mac producer admission and owner revocation; no installed-holder proof."""
from test_studio_producer_flow import (
    api,
    events,
    flow as _flow,
    runtime_app as _runtime_app,
    start,
    wait,
)

runtime_app = _runtime_app
flow = _flow


def test_actual_capacity_rejection_requires_explicit_attempt_bound_retry(flow, monkeypatch):
    app, _cookie, runtime, driver, offer, _source = flow
    sub = start(flow)
    calls, original = [], runtime.local._send

    def counted(*args):
        calls.append((args[0]['event_id'], args[0]['attempt']))
        return original(*args)

    monkeypatch.setattr(runtime.local, '_send', counted)
    # Hold actual engine progression, leaving real admitted inbox work pending.
    with app.ui.studio.lock:
        first = driver.call('publish', offer['id'], 'capacity-first', {'value': 1})
        assert first['state'] == 'done'
        rejected = driver.call('publish', offer['id'], 'capacity-second', {'value': 2})
        assert rejected['state'] == 'rejected', rejected
        assert len(events(flow, sub)) == 1
    wait(lambda: all(row['state'] == 'done' for row in events(flow, sub)))
    before = len(calls)
    assert driver.call('publish', offer['id'], 'capacity-second', {'value': 2})['state'] == 'rejected'
    assert len(calls) == before
    accepted = driver.call('retry', offer['id'], 'capacity-second', 'a' * 32)
    assert accepted['state'] == 'done'
    assert driver.call('retry', offer['id'], 'capacity-second', 'a' * 32)['receipt'] == accepted['receipt']
    assert len(events(flow, sub)) == 2
    assert [event for event, _ in calls] == ['capacity-first', 'capacity-second', 'capacity-second']
    assert calls[1][1] != calls[2][1]


def test_owner_revoke_stops_existing_data_channel_before_another_inbox_send(flow, monkeypatch):
    app, cookie, runtime, driver, offer, source = flow
    sub = start(flow)
    accepted = driver.call('publish', offer['id'], 'before-revoke', {'value': 1})
    assert accepted['state'] == 'done'
    wait(lambda: all(row['state'] == 'done' for row in events(flow, sub)))
    calls, original = [], runtime.local._send

    def counted(*args):
        calls.append(args[0]['event_id'])
        return original(*args)

    monkeypatch.setattr(runtime.local, '_send', counted)
    revoked = api(app, cookie, '/sources/' + source['id'] + '/revoke',
                  {'revision': source['revision'], 'idempotency_key': 'owner-revoke'})['source']
    assert not revoked['available']
    result = driver.command('publish', offer['id'], 'after-revoke', {'value': 2})
    assert result['ok'] is False, result
    status, reply, _ = app.call('/ui/studio/subscriptions/' + sub['id'] + '/events', cookie=cookie)
    assert status == 403 and 'revoked' in reply['error']
    assert calls == []
    store = app.ui.studio_streams.store
    actual = store.get('subscription', sub['id'], runtime.context['scope'])
    assert len(store.page(actual)['events']) == 1

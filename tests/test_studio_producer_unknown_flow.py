"""Conservative unknown transport bookkeeping across cancel/component recreation.

The transport fault occurs before socket send, not actual packet loss. Runtime
component recreation preserves the real store; this is not a daemon-process restart.
"""
import pytest
from test_studio_producer_flow import api, flow as _flow, runtime_app as _runtime_app, start

from poolhouse.fleet.producer_runtime import Runtime

runtime_app = _runtime_app
flow = _flow


@pytest.mark.parametrize('action', ['subscription-cancel', 'runtime-recreation'])
def test_unknown_publication_never_resends_or_adopts_after_lifecycle_change(flow, monkeypatch, action):
    app, cookie, runtime, driver, offer, source = flow
    sub = start(flow)
    calls = []

    def unavailable(*args):
        calls.append(args[0]['event_id'])
        raise OSError('Controlled transport failure without an acknowledgement')

    monkeypatch.setattr(runtime.local, '_send', unavailable)
    pending = driver.call('publish', offer['id'], 'unknown-record', {'value': 23})
    assert pending['state'] == 'interrupted' and 'receipt' not in pending
    assert driver.call('receipt', offer['id'], 'unknown-record')['state'] == 'interrupted'
    assert driver.call('publish', offer['id'], 'unknown-record', {'value': 23})['state'] == 'interrupted'
    assert driver.command('publish', offer['id'], 'bypass-record', {'value': 24})['ok'] is False
    assert calls == ['unknown-record']
    store = app.ui.studio_streams.store
    pointer_key = 'studio.producer-pending:' + source['id']
    pointer = store.graph.get_doc(pointer_key)
    assert pointer is not None
    intent = store.graph.get_doc(pointer['key'])
    assert intent['state'] == 'interrupted'
    actual_sub = store.get('subscription', sub['id'], runtime.context['scope'])
    assert store.page(actual_sub)['events'] == []
    replacement = None
    try:
        if action == 'subscription-cancel':
            cancelled = api(app, cookie, '/subscriptions/' + sub['id'] + '/cancel',
                            {'revision': sub['revision'], 'idempotency_key': 'cancel-unknown'})['subscription']
            assert cancelled['state'] == 'cancelled'
        else:
            original_instance = runtime.server.instance
            runtime.close()
            replacement = Runtime(app.ui, app.ui.studio_streams, runtime.local)
            assert replacement.server.instance != original_instance
            assert replacement.host.rows == {}
            assert replacement.available(store.get('source', source['id'], replacement.context['scope'])) is False
        assert driver.command('publish', offer['id'], 'unknown-record', {'value': 23})['ok'] is False
        assert store.graph.get_doc(pointer_key) == pointer
        assert store.graph.get_doc(pointer['key']) == intent
        assert store.page(actual_sub)['events'] == [] and calls == ['unknown-record']
    finally:
        if replacement is not None:
            replacement.close()

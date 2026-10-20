"""Current source authority between real selected nodes, without model inference."""
import threading

import pytest
from test_studio_producer_flow import api, events, flow as _flow, runtime_app as _runtime_app, wait

runtime_app = _runtime_app
flow = _flow


def start_nodes(flow):
    app, cookie, _runtime, _driver, _offer, source = flow
    workflow = api(app, cookie, '/workflows', {'title': 'Selected node authority', 'graph': {
        'nodes': [{'id': 'record', 'type': 'stream.record'},
                  {'id': 'first', 'type': 'prompt', 'parameters': {'template': 'First fixed output'}},
                  {'id': 'second', 'type': 'prompt', 'parameters': {'template': 'Second fixed output'}}],
        'edges': []}})['workflow']
    sub = api(app, cookie, '/subscriptions', {'workflow_id': workflow['id'], 'revision': workflow['revision'],
        'source_id': source['id'], 'input_node': 'record', 'output_nodes': ['record', 'first', 'second'],
        'destinations': [{'kind': 'live'}], 'idempotency_key': 'selected-sub'})['subscription']
    return api(app, cookie, '/subscriptions/' + sub['id'] + '/start',
               {'revision': sub['revision'], 'idempotency_key': 'selected-start'})['subscription']


@pytest.mark.parametrize('action', ['revoke', 'sdk-exit'])
def test_source_authority_between_selected_nodes_distinguishes_revoke_from_sdk_exit(flow, monkeypatch, action):
    app, cookie, runtime, driver, offer, source = flow
    sub = start_nodes(flow)
    store = app.ui.studio_streams.store
    original_source = store.get('source', source['id'], runtime.context['scope'])
    original_sub = store.get('subscription', sub['id'], runtime.context['scope'])
    entered, release, completed = threading.Event(), threading.Event(), []
    original = app.ui.studio.adapters.immediate

    def paused(node, inputs, context):
        result = original(node, inputs, context)
        if node['id'] == 'first':
            assert not store.lock._is_owned()
            entered.set()
            assert release.wait(10), 'The owner action did not release the selected-node barrier'
        completed.append(node['id'])
        return result

    monkeypatch.setattr(app.ui.studio.adapters, 'immediate', paused)
    try:
        accepted = driver.call('publish', offer['id'], 'node-authority', {'value': 19})
        assert accepted['state'] == 'done'
        assert entered.wait(5), 'The real first selected node was not executed'
        if action == 'revoke':
            changed = api(app, cookie, '/sources/' + source['id'] + '/revoke',
                          {'revision': source['revision'], 'idempotency_key': 'between-nodes'})['source']
            assert not changed['available']
            assert store.get('source', source['id'], runtime.context['scope']) != original_source
        else:
            assert driver.close()
            assert store.get('source', source['id'], runtime.context['scope']) == original_source
            actual_sub = store.get('subscription', sub['id'], runtime.context['scope'])
            assert actual_sub['grant'] == original_sub['grant']
    finally:
        release.set()
    actual = store.get('subscription', sub['id'], runtime.context['scope'])
    row = store.page(actual)['events'][0]
    run = wait(lambda: (value if value['state'] not in {'queued', 'running'} else None)
               if (value := app.ui.studio.store.get('run', row['run_id'], runtime.context['scope'])) else None)
    assert run['nodes']['first']['state'] == 'done'
    if action == 'revoke':
        assert run['nodes']['second']['state'] == 'blocked' and 'second' not in completed
        status, reply, _ = app.call('/ui/studio/subscriptions/' + sub['id'] + '/events', cookie=cookie)
        assert status == 403 and 'revoked' in reply['error']
    else:
        assert run['nodes']['second']['state'] == 'done' and 'second' in completed
        page = wait(lambda: (rows if rows and rows[0]['state'] == 'done' else None) if (rows := events(flow, sub)) else None)
        assert {item['node'] for item in page[0]['result']['outputs']} == {'record', 'first', 'second'}
        current = api(app, cookie, '/sources')['sources']
        assert not next(item for item in current if item['id'] == source['id'])['available']

"""Real owner HTTP, durable Studio runs and registered selected-preview datasets."""
import copy
import hashlib
import http.client
import json
import threading
import time
from pathlib import Path

import pytest
from test_fleet_ui import Serving

from poolhouse.fleet import dataset_catalogue, studio_epoch, tls
from poolhouse.fleet.discovery import mint_pool
from poolhouse.fleet.membership import Roster
from poolhouse.studio import dataset_samples as samples

ROOT = '/ui/workspace/datasets/'


@pytest.fixture
def served(tmp_path, monkeypatch):
    app = Serving(tmp_path)
    app.ui.root = tmp_path / 'traind'
    app.ui.settings.setup_done = True
    identity = tls.identity(tmp_path / 'tls', 'dataset-test')
    app.ui.studio_identity = lambda: identity
    member = mint_pool('dataset-test-pool', app.keyfile)
    Roster(member.key).enrol(identity.beacon, 'dataset-device', 'test')
    session = app.ui.sessions.open('dataset-owner', 'launch-ticket')
    preview = {'columns': [{'name': 'text', 'type': 'string'}, {'name': 'label', 'type': 'string'}],
        'rows': [{'text': 'first', 'label': 'yes'}, {'text': 'second', 'label': 'no'}],
        'total_rows': 500, 'live_preview': True, 'partial': False}
    calls = []
    def read(repo, config, split):
        calls.append((repo, config, split))
        return copy.deepcopy(preview)
    monkeypatch.setattr(dataset_catalogue, 'preview', read)
    body = {'repo': 'example/data', 'config': 'default', 'split': 'train', 'name': 'Selected preview rows',
        'idempotency_key': 'choose-once', 'row_indexes': [1], 'columns': {'text': 'prompt'},
        'expected_preview_digest': samples.preview_digest('example/data', 'default', 'train', preview)}
    app.cookie = 'poolhouse_ui=' + session.sid
    try:
        yield app, body, preview, calls, session
    finally:
        engine = getattr(app.ui, 'studio', None)
        if engine:
            engine.close()
            engine.store.close()
        app.close()


def submit(app, body):
    status, result, _ = app.call(ROOT + 'preview-imports', method='POST', cookie=app.cookie, body=body)
    assert status == 202, result
    return result['run']


def completed(app, ident):
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        status, result, _ = app.call(ROOT + 'imports/' + ident, cookie=app.cookie)
        assert status == 200, result
        if result['run']['state'] in {'done', 'failed', 'blocked', 'interrupted', 'cancelled'}:
            return result
        time.sleep(.02)
    pytest.fail('The bounded dataset import did not finish')


def test_actual_import_artifact_registration_binding_and_idempotency(served):
    app, body, _, calls, _ = served
    run = submit(app, body)
    result = completed(app, run['id'])
    assert result['run']['state'] == 'done', result
    dataset = result['dataset']
    assert dataset['source'] == 'huggingface-preview' and dataset['rows'] == 1
    assert dataset['columns'] == [{'name': 'prompt', 'type': 'string'}]
    assert dataset['preview_digest'] == body['expected_preview_digest']
    assert not Path(dataset['id']).is_absolute()
    path = app.runner.files_root / dataset['id']
    assert path.read_text() == '{"prompt":"second"}\n'
    assert str(app.runner.files_root) not in json.dumps(result)
    assert submit(app, body)['id'] == run['id'] and len(calls) == 1
    status, resources, _ = app.call('/ui/studio/resources', cookie=app.cookie)
    assert status == 200 and dataset['id'] in [item['id'] for item in resources['datasets']]
    status, files, _ = app.call('/ui/studio/runs/' + run['id'] + '/artifacts', cookie=app.cookie)
    assert status == 200, files
    assert {item['name'] for item in files['files']} == {'dataset.jsonl', 'dataset-import.json'}
    status, checked, _ = app.call('/ui/studio/validate', method='POST', cookie=app.cookie,
        body={'graph': {'nodes': [{'id': 'data', 'type': 'dataset', 'bindings': {'dataset': {'kind': 'dataset', 'id': dataset['id']}}}], 'edges': []}})
    assert status == 200 and checked['valid'] is True, checked
    status, local, _ = app.call(ROOT + 'local-preview?path=' + dataset['id'], cookie=app.cookie)
    assert status == 200 and local['rows'] == [{'prompt': 'second'}]


def test_changed_body_key_and_changed_preview_do_not_create_new_run(served):
    app, body, preview, _, _ = served
    first = submit(app, body)
    changed = dict(body, name='Different name')
    assert app.call(ROOT + 'preview-imports', method='POST', cookie=app.cookie, body=changed)[0] == 409
    preview['rows'][1]['text'] = 'new observation'
    changed = dict(body, idempotency_key='new-selection')
    status, _, _ = app.call(ROOT + 'preview-imports', method='POST', cookie=app.cookie, body=changed)
    assert status == 400
    assert submit(app, body)['id'] == first['id']


def test_copied_request_id_cannot_execute_another_run(served):
    app, body, _, _, _ = served
    first = submit(app, body)
    assert completed(app, first['id'])['run']['state'] == 'done'
    engine = app.ui.studio
    pointer = engine.store.graph.get_doc('studio.dataset-run:' + first['id'])
    request = engine.store.graph.get_doc('studio.dataset-import:' + pointer['request_id'])
    run = engine.store.get('run', first['id'], request['scope'])
    copy_run = engine.submit(run['principal'], {'id': 'b' * 32, 'revision': 1, 'graph': {'nodes': run['graph']['nodes'], 'edges': []}}, ['dataset'], 'copied-request')
    result = completed_copy(engine, copy_run['id'], request['scope'])
    assert result['nodes']['dataset']['state'] == 'failed'
    assert not (app.runner.files_root / 'studio' / copy_run['id'] / hashlib.sha256(b'dataset').hexdigest()[:24] / 'dataset').exists()


def completed_copy(engine, ident, scope):
    limit = time.monotonic() + 5
    while time.monotonic() < limit:
        run = engine.store.get('run', ident, scope)
        if run['state'] in {'done', 'failed', 'blocked', 'interrupted'}:
            return run
        time.sleep(.02)
    pytest.fail('Copied request did not reach a terminal state')


def test_revocation_during_preview_fetch_does_not_submit_or_publish(served, monkeypatch):
    app, body, preview, _, _ = served
    started, release = threading.Event(), threading.Event()
    def held(*_args):
        started.set()
        assert release.wait(3)
        return copy.deepcopy(preview)
    monkeypatch.setattr(dataset_catalogue, 'preview', held)
    replies = []
    worker = threading.Thread(target=lambda: replies.append(app.call(ROOT + 'preview-imports', method='POST', cookie=app.cookie, body=body)))
    worker.start()
    try:
        assert started.wait(3)
        authority = app.ui.studio_authority
        studio_epoch.invalidate(authority.root, [authority._context()['pool']], 'security-reset')
    finally:
        release.set()
        worker.join(4)
    assert not worker.is_alive() and replies[0][0] == 403
    assert not (app.runner.files_root / 'datasets' / 'huggingface').exists()


def test_registered_dataset_tampering_revocation_and_later_owner(served):
    app, body, _, _, old = served
    first = submit(app, body)
    result = completed(app, first['id'])
    assert result['run']['state'] == 'done'
    ident = result['dataset']['id']
    new = app.ui.sessions.open('later-owner', 'passphrase')
    app.ui.sessions.close(old.sid)
    app.cookie = 'poolhouse_ui=' + new.sid
    assert app.call(ROOT + 'imports/' + first['id'], cookie=app.cookie)[0] == 200
    path = app.runner.files_root / ident
    path.write_text('{"prompt":"tampered"}\n')
    assert app.call(ROOT + 'imports/' + first['id'], cookie=app.cookie)[0] == 400
    assert app.call(ROOT + 'local-preview?path=' + ident, cookie=app.cookie)[0] == 400
    status, resources, _ = app.call('/ui/studio/resources', cookie=app.cookie)
    assert status == 200 and ident not in [item['id'] for item in resources['datasets']]


def test_weak_session_and_unknown_managed_ids_never_fall_back(served):
    app, body, _, _, _ = served
    weak = app.ui.sessions.open('weak-dataset-reader', 'invite')
    status, _, _ = app.call(ROOT + 'preview-imports', method='POST', cookie='poolhouse_ui=' + weak.sid, body=body)
    assert status == 403
    assert app.call(ROOT + 'local-preview?path=datasets/huggingface/' + 'a' * 64 + '/dataset.jsonl', cookie=app.cookie)[0] == 400


@pytest.mark.parametrize('failure', ['submit', 'bind'])
def test_submission_and_binding_failure_roll_back_run_and_request(served, monkeypatch, failure):
    app, body, _, _, _ = served
    assert app.call(ROOT + 'preview?repo=example/data&config=default&split=train', cookie=app.cookie)[0] == 200
    service = app.ui.studio_datasets
    original_submit, original_put = service.engine.submit, service.store.put
    captured = []
    def fail_submit(*args):
        run = original_submit(*args)
        captured.append(args[0])
        if failure == 'submit':
            raise ValueError('injected submission failure')
        return run
    def fail_bind(kind, row):
        if failure == 'bind' and kind == 'dataset-import' and row['run_id'] is not None:
            raise ValueError('injected binding failure')
        return original_put(kind, row)
    monkeypatch.setattr(service.engine, 'submit', fail_submit)
    monkeypatch.setattr(service.store, 'put', fail_bind)
    assert app.call(ROOT + 'preview-imports', method='POST', cookie=app.cookie, body=body)[0] == 400
    scope = captured[0]['scope']
    assert service.store.records('dataset-import', scope) == []
    assert service.store.records('run', scope) == []
    assert not (app.runner.files_root / 'datasets' / 'huggingface').exists()


def test_scheduler_cannot_observe_unbound_request_after_wake(served, monkeypatch):
    app, body, _, _, _ = served
    assert app.call(ROOT + 'preview?repo=example/data&config=default&split=train', cookie=app.cookie)[0] == 200
    service = app.ui.studio_datasets
    original = service.engine.submit
    waiting, entered = threading.Event(), threading.Event()
    threads = []
    def scheduler():
        waiting.set()
        with service.engine.lock:
            entered.set()
            records = service.store.tracked_runs()
            for run in records:
                assert service.store.graph.get_doc('studio.dataset-run:' + run['id'])
    def submit_before_binding(*args):
        run = original(*args)
        thread = threading.Thread(target=scheduler)
        threads.append(thread)
        thread.start()
        assert waiting.wait(1)
        assert not entered.wait(.03)
        return run
    monkeypatch.setattr(service.engine, 'submit', submit_before_binding)
    try:
        run = submit(app, dict(body, idempotency_key='x' * 256))
        assert completed(app, run['id'])['run']['state'] == 'done'
    finally:
        for thread in threads:
            thread.join(2)
    assert entered.is_set() and all(not thread.is_alive() for thread in threads)


@pytest.mark.parametrize('placement', ['dataset', 'grouped-dataset', 'literal', 'wrong-kind', 'missing'])
def test_service_grant_requires_actual_typed_dataset_binding(served, monkeypatch, placement):
    app, body, _, _, _ = served
    run = submit(app, body)
    result = completed(app, run['id'])
    resource = result['dataset']['id']
    service = app.ui.studio_datasets
    row, actual_run = service._from_run(next(iter(service.store.records('run', service.authority._context()['scope'])))['principal'], run['id'])
    # Isolate this resource resolver from unrelated subscription source fixtures;
    # the actual authority still mints and checks its durable scope/epoch/grant.
    monkeypatch.setattr(service.authority, 'resources', lambda *_args: True)
    node = {'id': 'data', 'type': 'dataset', 'bindings': {'dataset': {'kind': 'dataset', 'id': resource}}}
    if placement == 'literal':
        node = {'id': 'literal', 'type': 'stream.record', 'parameters': {'sample': json.dumps({'id': resource})}}
    elif placement == 'wrong-kind':
        node = {'id': 'data', 'type': 'model', 'bindings': {'model': {'kind': 'model', 'id': resource}}}
    elif placement == 'missing':
        node = {'id': 'literal', 'type': 'stream.record', 'parameters': {'sample': '{}'}}
    manifest = {'workflow': {'id': 'grant-flow', 'revision': 1, 'graph': {'nodes': [node], 'edges': []}},
                'source': {}, 'destinations': [], 'resources': []}
    if placement == 'grouped-dataset':
        graph = manifest['workflow']['graph']
        graph['groups'] = {'selected': {'nodes': [node], 'edges': []}}
        graph['nodes'] = [{'id': 'nested', 'type': 'group', 'group': 'selected'}]
    grant = service.authority.mint(actual_run['principal'], 'isolated-subscription', manifest, 'start')
    principal = service.authority.principal(grant)
    binding = {'kind': 'dataset', 'id': resource}
    if placement in {'dataset', 'grouped-dataset'}:
        assert service.resolve(binding, {'principal': principal})['id'] == resource
        service.authority.revoke(actual_run['principal'], grant['id'])
    with pytest.raises(PermissionError):
        service.resolve(binding, {'principal': principal})
    assert row['principal'] == actual_run['principal']


def test_raw_workspace_cannot_bypass_import_registry_or_producer_epoch(served):
    app, body, _, _, _ = served
    run = submit(app, body)
    result = completed(app, run['id'])
    resource = result['dataset']['id']
    producer = 'studio/' + run['id'] + '/' + hashlib.sha256(b'dataset').hexdigest()[:24] + '/dataset/dataset.jsonl'
    alias = app.runner.files_root / 'dataset-alias.jsonl'
    alias.symlink_to(app.runner.files_root / resource)
    for path in (resource, producer, 'dataset-alias.jsonl'):
        assert app.call('/ui/workspace/file?path=' + path, cookie=app.cookie)[0] == 400
        assert app.call('/ui/workspace/files?path=' + str(Path(path).parent), cookie=app.cookie)[0] == (200 if path == 'dataset-alias.jsonl' else 400)
        assert app.call('/ui/workspace/file', method='POST', cookie=app.cookie,
                        body={'path': path, 'text': 'replacement'})[0] == 400
        if path != resource:
            graph = {'nodes': [{'id': 'data', 'type': 'dataset', 'bindings': {'dataset': {'kind': 'dataset', 'id': path}}}], 'edges': []}
            status, saved, _ = app.call('/ui/studio/workflows', method='POST', cookie=app.cookie,
                                        body={'title': 'Forbidden raw artifact', 'graph': graph})
            assert status == 201
            workflow = saved['workflow']
            status, _, _ = app.call('/ui/studio/runs', method='POST', cookie=app.cookie,
                body={'workflow_id': workflow['id'], 'revision': workflow['revision'], 'output_nodes': ['data'], 'idempotency_key': 'raw-path-denied'})
            assert status in {400, 403}
    status, listing, _ = app.call('/ui/workspace/files?path=datasets', cookie=app.cookie)
    assert status == 200 and 'huggingface' not in [entry['name'] for entry in listing['files']]
    sibling = 'datasets/huggingface-not-imported/ordinary.jsonl'
    assert app.call('/ui/workspace/file', method='POST', cookie=app.cookie, body={'path': sibling, 'text': '{}\n'})[0] == 201
    assert app.call('/ui/workspace/file?path=' + sibling, cookie=app.cookie)[0] == 200
    authority = app.ui.studio_authority
    studio_epoch.invalidate(authority.root, [authority._context()['pool']], 'security-reset')
    assert app.call(ROOT + 'local-preview?path=' + resource, cookie=app.cookie)[0] == 403
    for path in (resource, producer, 'dataset-alias.jsonl'):
        assert app.call('/ui/workspace/file?path=' + path, cookie=app.cookie)[0] == 400


def test_unregistered_precompletion_managed_bytes_are_not_raw_resources(served):
    app, _, _, _, _ = served
    path = 'datasets/huggingface/' + 'e' * 64 + '/dataset.jsonl'
    target = app.runner.files_root / path
    target.parent.mkdir(parents=True)
    target.write_text('{"not":"registered"}\n')
    assert app.call('/ui/workspace/file?path=' + path, cookie=app.cookie)[0] == 400
    assert app.call('/ui/workspace/files?path=' + str(Path(path).parent), cookie=app.cookie)[0] == 400
    assert app.call(ROOT + 'local-preview?path=' + path, cookie=app.cookie)[0] == 400



def test_lost_post_ack_readonly_key_lookup_attaches_without_resubmitting(served, monkeypatch):
    app, body, _, calls, _ = served
    assert app.call(ROOT + 'preview?repo=example/data&config=default&split=train', cookie=app.cookie)[0] == 200
    calls.clear()
    service = app.ui.studio_datasets
    accepted, release = threading.Event(), threading.Event()
    original = service.submit_preview
    runs = []
    def held_ack(principal, selection):
        run = original(principal, selection)
        runs.append(run['id'])
        accepted.set()
        assert release.wait(3)
        return run
    monkeypatch.setattr(service, 'submit_preview', held_ack)
    connection = http.client.HTTPConnection('127.0.0.1', app.port, timeout=3)
    try:
        connection.request('POST', ROOT + 'preview-imports', json.dumps(body),
                           {'Cookie': app.cookie, 'X-Poolhouse-UI': '1', 'Content-Type': 'application/json'})
        assert accepted.wait(3)
        connection.close()  # The committed submission's response is deliberately never received.
    finally:
        release.set()
        connection.close()
    status, result, _ = app.call(ROOT + 'imports?key=' + body['idempotency_key'], cookie=app.cookie)
    assert status == 200 and result['run']['id'] == runs[0]
    assert completed(app, runs[0])['dataset']['rows'] == 1
    assert app.call(ROOT + 'imports?key=' + body['idempotency_key'], cookie=app.cookie)[1]['run']['id'] == runs[0]
    assert len(calls) == 1
    assert app.call(ROOT + 'imports?key=absent', cookie=app.cookie)[0] == 404
    other = app.ui.sessions.open('other-import-owner', 'passphrase')
    assert app.call(ROOT + 'imports?key=' + body['idempotency_key'], cookie='poolhouse_ui=' + other.sid)[0] == 404
    for query in ('', 'key=', 'key=x&key=y', 'key=x&run=selected', 'key=' + 'x' * 257):
        assert app.call(ROOT + 'imports?' + query, cookie=app.cookie)[0] == 400
    authority = service.authority
    studio_epoch.invalidate(authority.root, [authority._context()['pool']], 'security-reset')
    assert app.call(ROOT + 'imports?key=' + body['idempotency_key'], cookie=app.cookie)[0] == 403
    assert len(calls) == 1



def test_lookup_rechecks_epoch_after_status_read(served, monkeypatch):
    app, body, _, _, _ = served
    submit(app, body)
    service = app.ui.studio_datasets
    original = service.status
    def revoked_status(principal, ident):
        result = original(principal, ident)
        studio_epoch.invalidate(service.authority.root, [service.authority._context()['pool']], 'security-reset')
        return result
    monkeypatch.setattr(service, 'status', revoked_status)
    assert app.call(ROOT + 'imports?key=' + body['idempotency_key'], cookie=app.cookie)[0] == 403

"""Dataset catalogue reads remain inside actual UI/current Studio authorization."""
import threading

import pytest
from test_fleet_ui import Serving

from poolhouse.fleet import dataset_routes
from poolhouse.fleet.discovery import mint_pool
from poolhouse.fleet.workspace_routes import WorkspaceRoutes
from poolhouse.studio.store import digest


@pytest.fixture
def served(tmp_path):
    app = Serving(tmp_path)
    app.ui.root = tmp_path / 'traind'
    app.ui.settings.setup_done = True
    mint_pool('catalogue-test-pool', app.keyfile)
    session = app.ui.sessions.open('dataset-browser', 'launch-ticket')
    try:
        yield app, 'poolhouse_ui=' + session.sid, session
    finally:
        engine = getattr(app.ui, 'studio', None)
        if engine:
            engine.close()
            engine.store.close()
        app.close()


@pytest.mark.parametrize('case', [
    ('selection?repo=example%2Fdata&config=default&split=train', 'selection', ('example/data', 'default', 'train'),
     {'version': 1, 'repo': 'example/data', 'revision': 'a' * 40, 'converted_revision': 'b' * 40,
      'config': 'default', 'split': 'train', 'files': [{'path': 'default/train/0.parquet', 'sha256': 'c' * 64, 'size': 12}]}),
    ('search?query=sample&task=text&language=en&sort=likes&cursor=page', 'search',
     {'query': 'sample', 'task': 'text', 'language': 'en', 'sort': 'likes', 'cursor': 'page'},
     {'datasets': [{'id': 'example/data', 'title': '<script>inert</script>'}], 'cursor': None}),
    ('details?repo=example%2Fdata', 'details', ('example/data',),
     {'id': 'example/data', 'revision': 'a' * 40, 'splits': []}),
    ('preview?repo=example%2Fdata&config=default&split=train', 'preview', ('example/data', 'default', 'train'),
     {'columns': [{'name': 'text', 'type': 'string'}], 'rows': [{'text': 'data'}], 'total_rows': 1}),
])
def test_fixed_read_routes_use_catalogue_and_preserve_public_shape(served, monkeypatch, case):
    tail, function, arguments, result = case
    app, cookie, _ = served
    calls = []
    def catalogue(*args, **kwargs):
        calls.append(kwargs if kwargs else args)
        return result
    monkeypatch.setattr(dataset_routes.dataset_catalogue, function, catalogue)
    status, body, _ = app.call('/ui/workspace/datasets/' + tail, cookie=cookie)
    assert status == 200, body
    if function == 'preview':
        expected = digest({'repo': 'example/data', 'config': 'default', 'split': 'train',
                           'columns': result['columns'], 'rows': result['rows']})
        assert body.pop('preview_digest') == expected
    assert body == result and calls == [arguments]


@pytest.mark.parametrize('query', ['repo=example/data&repo=other/data', 'repo=example/data&url=',
                                  'repo=example/data&token=secret', 'repo=example/data&path=/tmp/private'])
def test_unknown_duplicate_and_empty_unknown_query_refused(served, monkeypatch, query):
    app, cookie, _ = served
    monkeypatch.setattr(dataset_routes.dataset_catalogue, 'details', lambda *_: pytest.fail('invalid query reached network'))
    status, _, _ = app.call('/ui/workspace/datasets/details?' + query, cookie=cookie)
    assert status == 400


def test_missing_session_denied_before_catalogue(served, monkeypatch):
    app, _, _ = served
    monkeypatch.setattr(dataset_routes.dataset_catalogue, 'search', lambda **_: pytest.fail('unauthorized network'))
    status, _, _ = app.call('/ui/workspace/datasets/search')
    assert status == 401


def test_fixed_routes_reject_post_without_calling_catalogue(served, monkeypatch):
    app, cookie, _ = served
    monkeypatch.setattr(dataset_routes.dataset_catalogue, 'search', lambda **_: pytest.fail('POST network'))
    status, _, _ = app.call('/ui/workspace/datasets/search', method='POST', cookie=cookie, body={})
    assert status == 405


@pytest.mark.parametrize('failure', [False, True])
def test_revocation_during_catalogue_read_wins_over_success_and_failure(served, monkeypatch, failure):
    app, cookie, session = served
    started, release = threading.Event(), threading.Event()
    def held(**_query):
        started.set()
        assert release.wait(3)
        if failure:
            raise RuntimeError('private transport diagnostic must not escape')
        return {'datasets': [{'id': 'stale/data'}], 'cursor': None}
    monkeypatch.setattr(dataset_routes.dataset_catalogue, 'search', held)
    replies = []
    thread = threading.Thread(target=lambda: replies.append(app.call('/ui/workspace/datasets/search', cookie=cookie)))
    thread.start()
    try:
        assert started.wait(3)
        app.ui.sessions.close(session.sid)
    finally:
        release.set()
        thread.join(4)
    assert not thread.is_alive()
    assert replies[0][0] == 403
    assert 'stale' not in str(replies[0][1]) and 'private transport' not in str(replies[0][1])


def test_transport_failure_is_redacted(served, monkeypatch):
    app, cookie, _ = served
    def unavailable(**_query):
        raise OSError('secret=https://private.invalid/value')
    monkeypatch.setattr(dataset_routes.dataset_catalogue, 'search', unavailable)
    status, body, _ = app.call('/ui/workspace/datasets/search', cookie=cookie)
    assert status == 400
    assert 'secret' not in str(body) and 'private.invalid' not in str(body)


def test_existing_workspace_file_permission_behavior_unchanged(served, monkeypatch):
    app, cookie, _ = served
    def denied(_self, _root):
        raise PermissionError('File access denied')
    monkeypatch.setattr(WorkspaceRoutes, '_files', denied)
    status, body, _ = app.call('/ui/workspace/files', cookie=cookie)
    assert status == 400 and body['error'] == 'File access denied'


def test_truthy_authorization_is_not_current_authority(served, monkeypatch):
    app, cookie, _ = served
    monkeypatch.setattr(dataset_routes.dataset_catalogue, 'search', lambda **_: {'datasets': [], 'cursor': None})
    assert app.call('/ui/workspace/datasets/search', cookie=cookie)[0] == 200
    monkeypatch.setattr(app.ui.studio, 'authorize', lambda *_: 1)
    assert app.call('/ui/workspace/datasets/search', cookie=cookie)[0] == 403


def test_real_nested_managed_file_preview(served, monkeypatch):
    app, cookie, _ = served
    path = app.runner.files_root / 'datasets' / 'sample.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"text":"first"}\n{"text":"second"}\n')
    monkeypatch.setattr(dataset_routes.dataset_catalogue, 'preview', lambda *_: pytest.fail('local preview reached HF'))
    status, body, _ = app.call('/ui/workspace/datasets/local-preview?path=datasets%2Fsample.jsonl', cookie=cookie)
    assert status == 200, body
    assert body['path'] == 'datasets/sample.jsonl'
    assert body['rows'] == [{'text': 'first'}, {'text': 'second'}]
    assert body['sampled'] is True


@pytest.mark.parametrize('query', ['path=/tmp/private.jsonl', 'path=../private.jsonl',
                                  'path=datasets/a.jsonl&path=datasets/b.jsonl', 'path=&url=https://private.invalid'])
def test_local_preview_cannot_select_machine_or_extra_paths(served, query):
    app, cookie, _ = served
    status, _, _ = app.call('/ui/workspace/datasets/local-preview?' + query, cookie=cookie)
    assert status == 400

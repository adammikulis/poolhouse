"""Actual sampled-row import runs and resources through the browser; Hub is isolated."""
import json
import threading

import pytest
from browser_expect import expect
from test_dataset_catalogue import REPO, remote as _remote
from test_dataset_imports import served as _served
from test_studio_canvas_browser import studio_browser as _studio_browser

from poolhouse.fleet import dataset_catalogue
from poolhouse.fleet.session import parse_cookie

remote = _remote
served = _served
studio_browser = _studio_browser
pytestmark = pytest.mark.slow


@pytest.fixture
def import_page(served, remote, studio_browser):
    app, _body, _preview, _calls, session = served
    with studio_browser() as page:
        page.context.add_cookies([{'name': 'poolhouse_ui', 'value': session.sid, 'url': f'http://127.0.0.1:{app.port}'}])
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{app.port}/ui/#data')
        page.get_by_role('tab', name='Discover Hugging Face', exact=True).click()
        discovery = page.locator('data-discover')
        expect(discovery.locator('.dataset-result')).to_have_count(1)
        discovery.locator('.dataset-result').click()
        expect(discovery.get_by_role('heading', name=REPO, exact=True)).to_be_visible()
        expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_visible()
        yield page, app
        assert errors == []


def configure(page):
    page.get_by_label('Imported dataset name', exact=True).fill('Observed records')
    page.get_by_label('Import sample row 1', exact=True).uncheck()
    page.get_by_label('Import field label', exact=True).uncheck()
    page.get_by_label('Output field for text', exact=True).fill('prompt')


def test_selected_preview_rows_become_real_managed_dataset_and_studio_binding(import_page):
    page, app = import_page
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    configure(page)
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.locator('data-import .dataset-import-progress')).to_contain_text('1 imported rows')
    expect(page.locator('data-import .dataset-import-progress')).to_contain_text('observed preview data')
    op = page.evaluate("document.querySelector('data-import').operation")
    assert op['body']['row_indexes'] == [1]
    assert op['body']['columns'] == {'text': 'prompt'}
    dataset = op['dataset']
    assert dataset['source'] == 'huggingface-preview'
    assert app.files.joinpath(dataset['id']).read_text() == '{"prompt":"second"}\n'
    assert len(app.call('/ui/studio/runs', cookie=app.cookie)[1]['runs']) == 1
    page.get_by_role('button', name='Preview imported dataset', exact=True).click()
    expect(page.get_by_role('tab', name='Local datasets', exact=True)).to_have_attribute('aria-selected', 'true')
    expect(page.locator('data-view #preview-title')).to_have_text('Observed records')
    expect(page.locator('data-view #structured-preview')).to_contain_text('second')
    page.get_by_role('button', name='Use in Studio', exact=True).click()
    page.wait_for_function("() => document.querySelector('studio-view').graph.nodes.some(n=>n.type==='dataset')")
    graph = page.evaluate("document.querySelector('studio-view').graph")
    assert len(graph['nodes']) == 1
    assert graph['nodes'][0]['bindings'] == {'dataset': {'kind': 'dataset', 'id': dataset['id']}}
    assert not app.runner.jobs


def test_import_submission_body_stays_immutable_during_navigation(import_page):
    page, app = import_page
    configure(page)
    held = []
    page.route('**/ui/workspace/datasets/preview-imports', lambda route: held.append(route))
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_disabled()
    assert held
    original = json.loads(held[0].request.post_data)
    page.get_by_label('Imported dataset name', exact=True).fill('Different next name')
    page.get_by_role('tab', name='Local datasets', exact=True).click()
    response = held[0].fetch()
    held[0].fulfill(response=response)
    page.wait_for_function("() => document.querySelector('data-import').operation?.run?.id")
    page.get_by_role('tab', name='Discover Hugging Face', exact=True).click()
    expect(page.locator('data-import .dataset-import-progress')).to_contain_text('1 imported rows')
    op = page.evaluate("document.querySelector('data-import').operation")
    assert op['body'] == original and op['body']['name'] == 'Observed records'
    assert len(app.call('/ui/studio/runs', cookie=app.cookie)[1]['runs']) == 1


def test_failed_real_import_never_offers_dataset_preview(import_page, monkeypatch):
    page, app = import_page
    def fail(_node, _context):
        raise ValueError('Isolated import failure')
    monkeypatch.setattr(app.runner, 'studio_dataset_import', fail)
    configure(page)
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.locator('data-import .dataset-import-progress')).to_contain_text('failed')
    expect(page.get_by_role('button', name='Preview imported dataset', exact=True)).to_have_count(0)
    assert page.evaluate("document.querySelector('data-import').operation.dataset") is None
    assert not (app.files / 'datasets' / 'huggingface').exists()


def test_late_committed_submission_cannot_restore_handles_after_signout(import_page):
    page, app = import_page
    configure(page)
    held = []
    page.route('**/ui/workspace/datasets/preview-imports', lambda route: held.append(route))
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_disabled()
    assert held
    response = held[0].fetch()
    assert response.status == 202
    page.get_by_role('button', name='Sign out', exact=True).click()
    page.wait_for_function("() => ['sign-in','launch-needed'].includes(window.fleetModel.route)")
    with page.expect_response('**/ui/workspace/datasets/preview-imports') as reply:
        held[0].fulfill(response=response)
    reply.value.finished()
    page.evaluate('() => new Promise(resolve => setTimeout(resolve, 0))')
    assert page.evaluate("document.querySelector('data-import').operation") is None
    expect(page.get_by_role('button', name='Preview imported dataset', exact=True)).to_have_count(0)
    assert app.ui.studio.store.graph.get_doc('studio.run:'+response.json()['run']['id']) is not None


def test_committed_503_is_unknown_until_read_only_same_key_lookup(import_page):
    page, app = import_page
    configure(page)
    posts = []
    page.route('**/ui/workspace/datasets/preview-imports', lambda route: posts.append(route))
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_disabled()
    committed = posts[0].fetch()
    assert committed.status == 202
    posts[0].fulfill(status=503, json={'error': 'Isolated acknowledgement failure'})
    expect(page.locator('data-import .dataset-import-progress')).to_contain_text('unknown')
    expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_disabled()
    assert len(posts) == 1
    page.get_by_role('button', name='Check this import', exact=True).click()
    expect(page.locator('data-import .dataset-import-progress')).to_contain_text('1 imported rows')
    op = page.evaluate("document.querySelector('data-import').operation")
    assert op['run']['id'] == committed.json()['run']['id']
    assert len(posts) == 1 and len(app.call('/ui/studio/runs', cookie=app.cookie)[1]['runs']) == 1


def test_preview_resource_transport_failure_stays_in_owned_card(import_page):
    page, _app = import_page
    configure(page)
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.get_by_role('button', name='Preview imported dataset', exact=True)).to_be_visible()
    page.route('**/ui/studio/resources', lambda route: route.abort('failed'))
    page.get_by_role('button', name='Preview imported dataset', exact=True).click()
    expect(page.locator('data-import .dataset-import-progress')).to_contain_text('Failed to fetch')
    expect(page.get_by_role('tab', name='Discover Hugging Face', exact=True)).to_have_attribute('aria-selected', 'true')


def hold_first_preview(monkeypatch):
    original = dataset_catalogue.preview
    entered, release, lock = threading.Event(), threading.Event(), threading.Lock()
    reads = 0
    def preview(*args):
        nonlocal reads
        with lock:
            reads += 1
            first = reads == 1
        if first:
            entered.set()
            assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(dataset_catalogue, 'preview', preview)
    return entered, release


def test_explicit_same_key_resubmit_racing_original_request_creates_one_run(import_page, monkeypatch):
    page, app = import_page
    configure(page)
    entered, release = hold_first_preview(monkeypatch)
    responses, bodies, actors = [], [], []
    workers = []
    def forward(route):
        body = json.loads(route.request.post_data)
        bodies.append(body)
        if len(bodies) == 1:
            cookie = route.request.all_headers().get('cookie', '')
            actor = app.ui.sessions.get(parse_cookie(cookie))
            known_actor = actor is not None and actor.credentialed
            actors.append(known_actor)
            if not known_actor:
                route.abort('failed')
                return
            worker = threading.Thread(target=lambda: responses.append(app.call('/ui/workspace/datasets/preview-imports', method='POST', cookie=cookie, body=body)))
            workers.append(worker)
            worker.start()
            assert entered.wait(3)
            route.abort('failed')
        else:
            route.continue_()
    page.route('**/ui/workspace/datasets/preview-imports', forward)
    try:
        page.get_by_role('button', name='Import selected preview rows', exact=True).click()
        expect(page.locator('data-import .dataset-import-progress')).to_contain_text('unknown')
        assert actors == [True]
        page.get_by_role('button', name='Check this import', exact=True).click()
        expect(page.get_by_role('button', name='Submit this same selection', exact=True)).to_be_visible()
        page.get_by_role('button', name='Submit this same selection', exact=True).click()
        expect(page.locator('data-import .dataset-import-progress')).to_contain_text('1 imported rows')
        op = page.evaluate("document.querySelector('data-import').operation")
        assert bodies == [op['body'], op['body']]
    finally:
        release.set()
        for worker in workers:
            worker.join(5)
            assert not worker.is_alive()
    assert responses[0][0] == 202 and responses[0][1]['run']['id'] == op['run']['id']
    assert len(app.call('/ui/studio/runs', cookie=app.cookie)[1]['runs']) == 1


def test_registered_import_is_discoverable_after_page_reload(import_page):
    page, app = import_page
    configure(page)
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.locator('data-import .dataset-import-progress')).to_contain_text('1 imported rows')
    dataset = page.evaluate("document.querySelector('data-import').operation.dataset")
    page.goto(f'http://127.0.0.1:{app.port}/ui/#data')
    entry = page.locator('data-view #files').get_by_role('button').filter(has_text='Observed records')
    expect(entry).to_have_count(1)
    expect(entry).to_contain_text('1 rows · 1 fields')
    entry.click()
    expect(page.locator('data-view #preview-title')).to_have_text('Observed records')
    expect(page.locator('data-view #structured-preview')).to_contain_text('second')
    assert page.evaluate("document.querySelector('data-view').selected.managed") is True
    assert page.evaluate("document.querySelector('data-view').selected.path") == dataset['id']
    expect(page.get_by_role('button', name='Choose training recipe', exact=True)).to_be_hidden()


def test_discovery_uses_internal_panels_instead_of_body_scroll(import_page, remote):
    page, _app = import_page
    values, _calls = remote
    values['search'] = [values['main'], *[{**values['main'], 'id': f'sample-lab/records-{index}'} for index in range(23)]]
    page.set_viewport_size({'width':1440,'height':900})
    page.get_by_role('button', name='Search datasets', exact=True).click()
    results = page.locator('data-discover .dataset-results')
    expect(results.locator('.dataset-result')).to_have_count(24)
    assert page.locator('data-view h1').count() == 0
    assert page.evaluate('document.documentElement.scrollHeight<=window.innerHeight+1')
    assert results.evaluate('element=>element.scrollHeight>element.clientHeight')
    results.focus()
    page.keyboard.press('End')
    page.wait_for_function("() => document.querySelector('data-discover .dataset-results').scrollTop>0")
    results.locator('.dataset-result').first.click()
    expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_visible()
    inspector = page.locator('data-discover .dataset-import-content')
    assert inspector.evaluate('element=>element.scrollHeight>element.clientHeight')
    assert page.evaluate('document.documentElement.scrollHeight<=window.innerHeight+1')
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    assert page.evaluate('document.documentElement.scrollHeight<=window.innerHeight+1')
    assert inspector.evaluate('element=>element.clientHeight>0')
    assert results.evaluate('element=>element.clientHeight>0')


def test_detached_import_form_cannot_submit_a_new_split(import_page, remote):
    page, app = import_page
    values, _calls = remote
    values['splits']['splits'].append({'dataset': REPO, 'config': 'default', 'split': 'validation'})
    values['size']['size']['splits'].append({'dataset': REPO, 'config': 'default', 'split': 'validation', 'num_rows': 7, 'num_bytes_parquet': 17})
    page.locator('data-discover .dataset-result').click()
    expect(page.get_by_label('Dataset split', exact=True).locator('option')).to_have_count(2)
    expect(page.get_by_role('button', name='Import selected preview rows', exact=True)).to_be_visible()
    configure(page)
    page.evaluate("window.detachedDatasetSubmit=document.querySelector('data-import .data-primary')")
    posts = []
    page.on('request', lambda request: posts.append(request) if request.method == 'POST' and request.url.endswith('/datasets/preview-imports') else None)
    page.get_by_label('Dataset split', exact=True).select_option('validation')
    page.wait_for_function("() => document.querySelector('data-import').selection?.params.split==='validation'")
    page.evaluate('() => { window.detachedDatasetSubmit.click(); return new Promise(resolve=>setTimeout(resolve,0)); }')
    assert page.evaluate("document.querySelector('data-import').operation") is None
    assert posts == []
    assert app.call('/ui/studio/runs', cookie=app.cookie)[1]['runs'] == []


def test_registry_denial_clears_managed_handles_but_keeps_loose_files(import_page):
    page, app = import_page
    app.files.joinpath('loose.jsonl').write_text('{"text":"Loose local record"}\n')
    configure(page)
    page.get_by_role('button', name='Import selected preview rows', exact=True).click()
    expect(page.get_by_role('button', name='Preview imported dataset', exact=True)).to_be_visible()
    page.get_by_role('button', name='Preview imported dataset', exact=True).click()
    expect(page.locator('data-view #preview-title')).to_have_text('Observed records')
    expect(page.locator('data-view #files').get_by_role('button').filter(has_text='Observed records')).to_have_count(1)
    assert page.evaluate("document.querySelector('data-view').selected.managed") is True
    page.route('**/ui/studio/resources', lambda route: route.fulfill(status=403, json={'error': 'Imported inventory denied'}))
    page.get_by_role('tab', name='Local datasets', exact=True).click()
    expect(page.locator('data-view .status')).to_have_text('Imported inventory denied')
    expect(page.get_by_role('button', name='Use in Studio', exact=True)).to_be_hidden()
    assert page.evaluate("document.querySelector('data-view').selected") is None
    expect(page.locator('data-view #raw-preview')).to_be_hidden()
    page.locator('data-view #files').get_by_role('button').filter(has_text='loose.jsonl').click()
    expect(page.locator('data-view #structured-preview')).to_contain_text('Loose local record')
    expect(page.get_by_role('button', name='Use in Studio', exact=True)).to_be_visible()

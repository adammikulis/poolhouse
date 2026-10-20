"""Dataset discovery uses actual authenticated routes with isolated Hub responses."""
import json

import pytest
from browser_expect import expect
from test_dataset_catalogue import REPO, remote as _remote
from test_fleet_ui import Serving
from test_studio_canvas_browser import add, studio_browser as _studio_browser

from poolhouse.fleet.discovery import mint_pool

remote = _remote
studio_browser = _studio_browser
pytestmark = pytest.mark.slow


@pytest.fixture
def dataset_page(tmp_path, remote, studio_browser):
    app = Serving(tmp_path)
    app.ui.root = tmp_path / 'traind'
    app.ui.settings.setup_done = True
    mint_pool('dataset-ui-pool', app.keyfile)
    session = app.ui.sessions.open('dataset-browser', 'launch-ticket')
    try:
        with studio_browser() as page:
            page.context.add_cookies([{'name': 'poolhouse_ui', 'value': session.sid, 'url': f'http://127.0.0.1:{app.port}'}])
            errors, requests = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            page.goto(f'http://127.0.0.1:{app.port}/ui/#data')
            expect(page.get_by_role('tab', name='Local datasets', exact=True)).to_have_attribute('aria-selected', 'true')
            yield page, app, errors, requests, remote
            assert errors == []
    finally:
        try:
            if engine := getattr(app.ui, 'studio', None):
                engine.close()
                engine.store.close()
        finally:
            app.close()


def test_hugging_face_search_filters_split_sample_license_and_pins(dataset_page):
    page, _app, _errors, requests, remote_state = dataset_page
    page.get_by_role('tab', name='Discover Hugging Face', exact=True).click()
    discovery = page.locator('data-discover')
    discovery.get_by_role('searchbox', name='Search Hugging Face', exact=True).fill('policy')
    discovery.get_by_label('Dataset task', exact=True).select_option('text-generation')
    discovery.get_by_label('Dataset language code', exact=True).fill('en')
    discovery.get_by_role('button', name='Search datasets', exact=True).click()
    expect(discovery.locator('.dataset-result')).to_have_count(1)
    discovery.locator('.dataset-result').click()
    expect(discovery.get_by_role('heading', name=REPO, exact=True)).to_be_visible()
    expect(discovery.locator('.dataset-detail')).to_contain_text('Licence: mit')
    expect(discovery.get_by_label('Dataset subset', exact=True)).to_have_value('default')
    expect(discovery.get_by_label('Dataset split', exact=True)).to_have_value('train')
    expect(discovery.locator('.dataset-sample')).to_contain_text('1 of 3 rows')
    expect(discovery.locator('.dataset-sample')).to_contain_text('not an immutable file preview')
    expect(discovery.locator('.dataset-revision')).to_contain_text('b' * 40)
    expect(discovery.locator('.dataset-sample')).to_contain_text('<script>bad()</script>')
    assert discovery.locator('img,iframe').count() == 0
    assert not any('/private' in request for request in requests)
    assert any('task_categories%3Atext-generation' in url and 'language%3Aen' in url for url in remote_state[1])
    page.get_by_role('tab', name='Discover Hugging Face', exact=True).focus()
    page.keyboard.press('ArrowLeft')
    expect(page.get_by_role('tab', name='Local datasets', exact=True)).to_have_attribute('aria-selected', 'true')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('tab', name='Discover Hugging Face', exact=True).click()
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')


@pytest.mark.parametrize('status', [200, 403])
def test_late_discovery_answer_cannot_replace_local_view_or_sign_out(dataset_page, status):
    page, _app, _errors, _requests, _remote_state = dataset_page
    held = []
    page.route('**/ui/workspace/datasets/search?**', lambda route: held.append(route))
    page.get_by_role('tab', name='Discover Hugging Face', exact=True).click()
    expect(page.locator('data-discover .dataset-status')).to_have_text('Searching Hugging Face…')
    page.wait_for_function("() => document.querySelector('data-discover').active===true")
    page.get_by_role('tab', name='Local datasets', exact=True).click()
    assert held
    with page.expect_response('**/ui/workspace/datasets/search?**') as response:
        held[0].fulfill(status=status, json={'datasets': [{'id': 'stale-lab/answer', 'title': 'Stale answer'}], 'cursor': None} if status == 200 else {'error': 'Late denial'})
    response.value.finished()
    page.evaluate('() => new Promise(resolve => setTimeout(resolve, 0))')
    expect(page.get_by_role('tab', name='Local datasets', exact=True)).to_have_attribute('aria-selected', 'true')
    assert page.evaluate("window.fleetModel.route==='data'")
    assert 'Stale answer' not in page.locator('data-discover .dataset-results').inner_text()


def test_local_csv_and_nested_jsonl_inspection_adds_one_bound_node_without_replacing_workflow(dataset_page):
    page, app, _errors, _requests, _remote_state = dataset_page
    folder = app.files / 'datasets' / 'nested'
    folder.mkdir(parents=True)
    row = {'text': '<img src=x>', 'nested': {'instruction': 'Literal source content'}, **{f'field-{index}': index for index in range(12)}}
    (folder / 'records.jsonl').write_text(json.dumps(row) + '\n')
    (folder / 'records.csv').write_text('question,response\n"Which, row?","First\nsecond"\n')
    page.locator('a[href="#studio"]').first.click()
    add(page, 'Prompt template')
    page.locator('.studio-inspector').get_by_label('template', exact=True).fill('Keep this existing text')
    page.get_by_label('Workflow title', exact=True).fill('Retained work')
    page.get_by_label('Workflow title', exact=True).press('Tab')
    before = page.evaluate("JSON.parse(JSON.stringify(document.querySelector('studio-view').graph))")
    page.locator('a[href="#data"]').first.click()
    files = page.locator('data-view #files')
    files.get_by_role('button').filter(has_text='datasets').click()
    files.get_by_role('button').filter(has_text='nested').click()
    files.get_by_role('button').filter(has_text='records.csv').click()
    sample = page.locator('data-view #structured-preview')
    expect(sample).to_contain_text('Which, row?')
    expect(sample).to_contain_text('First\nsecond')
    expect(page.get_by_role('button', name='Use in Studio', exact=True)).to_be_hidden()
    files.get_by_role('button').filter(has_text='records.jsonl').click()
    expect(sample.get_by_role('columnheader')).to_have_count(14)
    sample.get_by_label('Show field field-11', exact=True).uncheck()
    expect(sample.get_by_role('columnheader')).to_have_count(13)
    sample.get_by_label('Show field field-11', exact=True).check()
    sample.get_by_text('Object · 1 fields', exact=True).click()
    expect(sample).to_contain_text('Literal source content')
    assert sample.locator('img').count() == 0
    page.get_by_role('button', name='Use in Studio', exact=True).click()
    expect(page).to_have_url(f'http://127.0.0.1:{app.port}/ui/#studio')
    page.wait_for_function("() => document.querySelector('studio-view').graph.nodes.some(n=>n.type==='dataset')")
    after = page.evaluate("JSON.parse(JSON.stringify(document.querySelector('studio-view').graph))")
    assert after['nodes'][:-1] == before['nodes'] and after['edges'] == before['edges']
    assert after['nodes'][-1]['bindings'] == {'dataset': {'kind': 'dataset', 'id': 'datasets/nested/records.jsonl'}}
    assert len(after['nodes']) == len(before['nodes']) + 1 and app.runner.jobs == {}
    page.get_by_role('button', name='Undo', exact=True).click()
    assert page.evaluate("document.querySelector('studio-view').graph.nodes") == before['nodes']


@pytest.mark.parametrize('selection', ['save', 'new', 'identical-open'])
def test_pending_studio_handoff_tracks_workflow_ownership(dataset_page, selection):
    page, app, _errors, _requests, _remote_state = dataset_page
    app.files.joinpath('records.jsonl').write_text('{"text":"A record"}\n')
    page.locator('a[href="#studio"]').first.click()
    page.wait_for_function("() => document.querySelector('studio-view').loaded && !document.querySelector('studio-view').loading")
    page.locator('a[href="#data"]').first.click()
    page.locator('data-view #files').get_by_role('button').filter(has_text='records.jsonl').click()
    expect(page.get_by_role('button', name='Use in Studio', exact=True)).to_be_visible()
    held = []
    def hold_resources(route):
        held.append(route)
        page.evaluate('window.datasetResourcesHeld=true')
    page.route('**/ui/studio/resources', hold_resources)
    page.get_by_role('button', name='Use in Studio', exact=True).click()
    page.wait_for_function('() => window.datasetResourcesHeld===true')
    assert held
    if selection == 'save':
        page.evaluate("() => document.querySelector('studio-view').save()")
        assert page.evaluate("Boolean(document.querySelector('studio-view').workflowId)")
    else:
        page.evaluate("kind => { const s=document.querySelector('studio-view'); return s.open(kind==='new'?null:{id:'other-workflow',revision:1,title:'Identical graph',graph:structuredClone(s.graph)}); }", selection)
        page.evaluate("document.querySelector('studio-view').message('Current workflow status')")
        page.wait_for_function("() => !document.querySelector('data-view').studioHandoff")
        expect(page.locator('studio-view .studio-status')).to_have_text('Current workflow status')
    held[0].continue_()
    page.wait_for_function("() => !document.querySelector('studio-view').loading")
    if selection == 'save':
        page.wait_for_function("() => document.querySelector('studio-view').graph.nodes.some(n=>n.type==='dataset')")
        assert page.evaluate("document.querySelector('studio-view').graph.nodes.length") == 1
    else:
        assert page.evaluate("document.querySelector('studio-view').graph.nodes.length") == 0
    assert not app.runner.jobs


@pytest.mark.parametrize('status', [200, 403])
def test_late_upload_cannot_navigate_or_sign_out(dataset_page, status):
    page, _app, _errors, _requests, _remote_state = dataset_page
    page.get_by_role('button', name='+ Add dataset').click()
    page.get_by_label('Dataset name', exact=True).fill('pending')
    page.get_by_label('Or paste dataset content').fill('{"text":"pending"}\n')
    held = []
    page.route('**/ui/workspace/file', lambda route: held.append(route))
    page.get_by_role('button', name='Upload', exact=True).click()
    expect(page.get_by_role('button', name='Uploading…', exact=True)).to_be_disabled()
    page.locator('a[href="#studio"]').first.click()
    assert held
    with page.expect_response('**/ui/workspace/file') as response:
        held[0].fulfill(status=status, json={'ok': True} if status == 200 else {'error': 'Late denied upload'})
    response.value.finished()
    page.wait_for_function("() => !document.querySelector('data-view').pending")
    expect(page).to_have_url(f'http://127.0.0.1:{_app.port}/ui/#studio')
    assert page.evaluate("window.fleetModel.route==='studio'")
    assert page.locator('data-view #preview-title').inner_text() == 'Dataset preview'


@pytest.mark.parametrize('status', [200, 403])
def test_upload_completion_preserves_newer_local_preview(dataset_page, status):
    page, app, _errors, _requests, _remote_state = dataset_page
    app.files.joinpath('current.jsonl').write_text('{"text":"Current selection"}\n')
    page.get_by_role('button', name='+ Add dataset').click()
    page.get_by_label('Dataset name', exact=True).fill('older-upload')
    page.get_by_label('Or paste dataset content').fill('{"text":"Older upload"}\n')
    held = []
    page.route('**/ui/workspace/file', lambda route: held.append(route))
    page.get_by_role('button', name='Upload', exact=True).click()
    expect(page.get_by_role('button', name='Uploading…', exact=True)).to_be_disabled()
    page.locator('data-view #files').get_by_role('button').filter(has_text='current.jsonl').click()
    expect(page.locator('data-view #preview-title')).to_have_text('current.jsonl')
    with page.expect_response('**/ui/workspace/file') as response:
        held[0].fulfill(status=status, json={'ok': True} if status == 200 else {'error': 'Late denied upload'})
    response.value.finished()
    page.wait_for_function("() => !document.querySelector('data-view').pending")
    expect(page.locator('data-view #preview-title')).to_have_text('current.jsonl')
    expect(page.locator('data-view #structured-preview')).to_contain_text('Current selection')
    assert page.evaluate("document.querySelector('data-view').path") == ''
    assert page.evaluate("window.fleetModel.route==='data'")


def test_generated_filename_tracks_format_without_overwriting_explicit_location(dataset_page):
    page, _app, _errors, _requests, _remote_state = dataset_page
    page.get_by_role('button', name='+ Add dataset').click()
    page.get_by_label('Dataset name', exact=True).fill('Friendly examples')
    page.get_by_role('button', name='Choose a file', exact=True).click()
    upload = page.get_by_label('Choose a text / JSONL file')
    upload.set_input_files({'name': 'rows.csv', 'mimeType': 'text/csv', 'buffer': b'text\nExample\n'})
    path = page.get_by_label('Destination relative to files root')
    expect(path).to_have_value('datasets/friendly-examples.csv')
    page.get_by_text('File location', exact=True).click()
    path.fill('datasets/explicit.jsonl')
    page.get_by_label('Dataset name', exact=True).fill('Changed display name')
    upload.set_input_files({'name': 'other.json', 'mimeType': 'application/json', 'buffer': b'[]'})
    expect(path).to_have_value('datasets/explicit.jsonl')

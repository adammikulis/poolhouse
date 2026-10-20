"""Studio graph editing and durable run controls through the application."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import contextmanager

import pytest
from browser_expect import expect
from test_fleet_ui import Serving

from poolhouse.fleet import studio_service
from poolhouse.scrape.browser import Window, browser

pytestmark = pytest.mark.slow

SPECS = [
    {'type': 'prompt', 'version': 1, 'title': 'Prompt template', 'family': 'Inference',
     'inputs': [], 'outputs': [{'name': 'text', 'type': 'text'}],
     'parameters': {'properties': {'template': {'type': 'string', 'default': 'Hello', 'title': 'Prompt text'}}}},
    {'type': 'report', 'version': 1, 'title': 'Report', 'family': 'Results',
     'inputs': [{'name': 'text', 'type': 'text'}], 'outputs': [],
     'parameters': {'properties': {
         'title': {'type': 'string', 'default': 'Results'},
         'iterations': {'type': 'integer', 'default': 1},
         'mode': {'type': 'string', 'enum': ['smoke', 'train'], 'default': 'smoke'},
         'enabled': {'type': 'boolean', 'default': False}}}},
]

SPECS.extend([
    {'type': 'language-example', 'version': 1, 'title': 'Language producer', 'family': 'Inference',
     'inputs': [], 'outputs': [{'name': 'model', 'type': 'language-model'}],
     'parameters': {'properties': {}}},
    {'type': 'model-example', 'version': 1, 'title': 'Model consumer', 'family': 'Inference',
     'inputs': [{'name': 'model', 'type': 'model', 'accepted_types': ['model', 'language-model']}],
     'outputs': [], 'parameters': {'properties': {}}},
])


@pytest.fixture(scope='module')
def studio_browser(tmp_path_factory):
    """Keep one managed process per worker, with a fresh context for every journey."""
    pytest.importorskip('playwright.sync_api')
    profile = tmp_path_factory.mktemp('studio-managed-browser')
    with browser(Window(profile=profile, headless=True)) as managed_page:
        managed_browser = managed_page.context.browser
        assert managed_browser is not None

        @contextmanager
        def isolated_page():
            context = managed_browser.new_context(
                viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            context.set_default_timeout(60_000)
            try:
                yield context.new_page()
            finally:
                context.close()

        yield isolated_page


@pytest.fixture
def studio_page(tmp_path, studio_browser):
    pytest.importorskip('playwright.sync_api')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    state = {'workflows': [], 'runs': [], 'submissions': [], 'saved': []}
    with studio_browser() as page:
        page.set_viewport_size({'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def route(request):
            path = request.request.url.split('/ui/studio', 1)[1]
            data = request.request.post_data_json if request.request.method != 'GET' else None
            if path == '/nodes':
                result = {'nodes': SPECS}
            elif path == '/templates':
                result = {'templates': []}
            elif path == '/workflows' and data is None:
                result = {'workflows': state['workflows']}
            elif path.startswith('/workflows'):
                if state.get('save_error'):
                    request.fulfill(status=503, json={'error': state['save_error']})
                    return
                workflow = {'id': 'workflow-1', 'revision': len(state['saved']) + 1, **data}
                workflow['revision'] = len(state['saved']) + 1
                state['saved'].append(workflow)
                state['workflows'] = [workflow]
                result = {'workflow': workflow}
            elif path == '/validate':
                result = {'valid': True, 'errors': []}
            elif path == '/runs' and data is not None:
                state['submissions'].append(data)
                run = {'id': 'run-1', 'workflow_id': data['workflow_id'], 'revision': data['revision'],
                       'state': 'done', 'nodes': {}}
                state['runs'] = [run]
                if state.pop('disconnect_next', False):
                    request.abort()
                    return
                result = {'run': run}
            elif path == '/runs':
                result = {'runs': state['runs']}
            elif path == '/runs/run-1':
                result = {'run': state['runs'][0]}
            else:
                result = {}
            request.fulfill(json=result)
        page.route('**/ui/studio/**', route)
        try:
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            expect(page.locator('studio-view > section')).to_be_visible()
            yield page, state, errors
        finally:
            served.close()


def add(page, title):
    name = title if isinstance(title, re.Pattern) else 'Add ' + title
    button = page.locator('.studio-library').get_by_role('button', name=name, exact=isinstance(name, str))
    button.focus()
    button.press('Enter')


def test_build_connect_configure_run_and_snapshot(studio_page):
    page, state, errors = studio_page
    add(page, 'Prompt template')
    add(page, 'Report')
    page.locator('.studio-inspector').get_by_label('Connect text').select_option(index=1)
    expect(page.locator('.studio-connections path')).to_have_count(1)
    page.get_by_label('Workflow title').fill('Experiment')
    page.get_by_label('Workflow title').press('Tab')
    state['disconnect_next'] = True
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Failed')
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('#studio-runs')).to_contain_text('done')
    assert len(state['submissions']) == 2
    assert state['submissions'][0]['idempotency_key'] == state['submissions'][1]['idempotency_key']
    assert state['saved'][-1]['graph']['edges'][0]['target']['port'] == 'text'
    first = state['submissions'][0]
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('submitted')
    assert state['submissions'][-1]['idempotency_key'] != first['idempotency_key']
    assert not errors


def test_keyboard_copy_group_nested_edit_and_outline(studio_page):
    page, _state, errors = studio_page
    add(page, 'Prompt template')
    page.locator('.studio-node').focus()
    page.keyboard.press('Control+a')
    page.get_by_role('button', name='Group selection', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(1)
    page.get_by_role('button', name='Open group', exact=True).first.click()
    expect(page.get_by_label('Group name')).to_be_visible()
    page.locator('.studio-node').focus()
    page.keyboard.press('Enter')
    page.get_by_label('Expose parameter template').fill('prompt')
    page.get_by_label('Expose parameter template').press('Tab')
    page.locator('.studio-viewport').focus()
    page.keyboard.press('Escape')
    expect(page.get_by_label('Group name')).to_have_count(0)
    page.get_by_role('button', name='Copy', exact=True).click()
    page.get_by_role('button', name='Paste', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(2)
    page.get_by_role('button', name='Outline', exact=True).click()
    expect(page.locator('.studio-outline')).to_be_visible()
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors


def test_node_labels_and_prompt_text_remain_inert(studio_page):
    page, _state, errors = studio_page
    add(page, 'Prompt template')
    value = '<img src=x onerror="window.studioInjected=true">'
    page.locator('.studio-inspector').get_by_label('Prompt text', exact=True).fill(value)
    page.locator('.studio-inspector').get_by_label('Prompt text', exact=True).press('Tab')
    page.get_by_role('button', name='Share', exact=True).click()
    expect(page.locator('#studio-portable')).to_contain_text(value)
    assert page.locator('#studio-portable img').count() == 0
    assert not page.evaluate('Boolean(window.studioInjected)')
    assert not errors


def test_nested_group_copies_and_exposed_parameter_undo(studio_page):
    page, _state, errors = studio_page
    add(page, 'Prompt template')
    page.get_by_role('button', name='Group selection', exact=True).click()
    page.get_by_role('button', name='Group selection', exact=True).click()
    page.get_by_role('button', name='Copy', exact=True).click()
    page.get_by_role('button', name='Paste', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(2)
    page.locator('.studio-node').first.get_by_role('button', name='Open group').click()
    page.locator('.studio-node').get_by_role('button', name='Open group').click()
    page.locator('.studio-node').focus()
    page.keyboard.press('Enter')
    field = page.get_by_label('Expose parameter template')
    field.fill('shared_prompt')
    field.press('Tab')
    page.get_by_role('button', name='Undo', exact=True).click()
    page.locator('.studio-node').focus()
    page.keyboard.press('Enter')
    expect(page.get_by_label('Expose parameter template')).to_have_value('')
    page.get_by_role('button', name='Redo', exact=True).click()
    page.locator('.studio-node').focus()
    page.keyboard.press('Enter')
    expect(page.get_by_label('Expose parameter template')).to_have_value('shared_prompt')
    page.locator('.studio-inspector').get_by_label('Prompt text', exact=True).fill('Changed original')
    page.locator('.studio-inspector').get_by_label('Prompt text', exact=True).press('Tab')
    page.get_by_role('button', name='← Back to parent').click()
    page.get_by_role('button', name='← Back to parent').click()
    page.locator('.studio-node').nth(1).get_by_role('button', name='Open group').click()
    page.locator('.studio-node').get_by_role('button', name='Open group').click()
    page.locator('.studio-node').focus()
    page.keyboard.press('Enter')
    expect(page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)).to_have_value('Hello')
    assert not errors


def test_narrow_screen_uses_outline_without_canvas(studio_page):
    page, _state, errors = studio_page
    add(page, 'Prompt template')
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(page.locator('.studio-outline')).to_be_visible()
    expect(page.locator('studio-canvas')).not_to_be_visible()
    page.locator('.studio-outline').get_by_role('button', name='Prompt template').click()
    expect(page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)).to_have_value('Hello')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors


def make_prompt_group(page):
    add(page, 'Prompt template')
    page.locator('.studio-inspector').get_by_label('template', exact=True).fill('Reusable prompt')
    page.locator('.studio-inspector').get_by_label('template', exact=True).press('Tab')
    page.get_by_role('button', name='Group selection', exact=True).click()
    page.locator('.studio-node').get_by_role('button', name='Open group').click()
    page.locator('.studio-node').focus()
    page.keyboard.press('Enter')
    page.get_by_label('Expose outputs prompt').fill('prompt')
    page.get_by_label('Expose outputs prompt').press('Tab')
    page.get_by_role('button', name='← Back to parent').click()


def test_actual_catalogue_template_run_and_package_round_trip(tmp_path, studio_browser):
    pytest.importorskip('playwright.sync_api')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.root = tmp_path / 'traind'
    served.files.joinpath('examples.jsonl').write_text('{"prompt":"Example","response":"Answer"}\n')
    try:
        with studio_browser() as page:
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_viewport_size({'width': 1440, 'height': 1000})
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            add(page, re.compile('fine tuning and evaluation', re.IGNORECASE))
            expect(page.locator('.studio-node')).to_have_count(9)
            page.locator('.studio-node').filter(has=page.get_by_text('Dataset', exact=True)).first.focus()
            page.keyboard.press('Enter')
            page.locator('.studio-inspector').get_by_label('Choose dataset').select_option('examples.jsonl')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('.studio-status')).to_contain_text('Saved')
            page.get_by_role('button', name='New', exact=True).click()
            make_prompt_group(page)
            page.get_by_role('button', name='Run workflow', exact=True).click()
            expect(page.locator('#studio-runs')).to_contain_text('done', timeout=10000)
            page.get_by_role('button', name='Run workflow', exact=True).click()
            expect(page.locator('.studio-run-row')).to_have_count(2)
            page.get_by_role('button', name='Share', exact=True).click()
            page.get_by_label('Package content').select_option('selection')
            page.locator('#studio-portable input[type=checkbox]').check()
            with page.expect_download() as download:
                page.get_by_role('button', name='Export file', exact=True).click()
            package_path = download.value.path()
            page.get_by_role('button', name='Import', exact=True).click()
            page.locator('#studio-import-file').set_input_files(package_path)
            expect(page.locator('#studio-import-preview')).to_contain_text('Import adds configuration only')
            page.get_by_role('button', name='Import configuration', exact=True).click()
            expect(page.locator('.studio-status')).to_contain_text('Package imported')
            expect(page.locator('.studio-node')).to_have_count(1)
            page.get_by_role('button', name='Run workflow', exact=True).click()
            expect(page.locator('.studio-run-row')).to_have_count(3)
            assert not errors
    finally:
        if hasattr(served.ui, 'studio'):
            served.ui.studio.close()
        served.close()


def hold_next_save(page):
    pending = []
    def hold(request):
        if pending:
            request.continue_()
            return
        pending.append(request)
        page.evaluate("() => { window.studioSaveHeld = true; }")
    page.route('**/ui/studio/workflows/*', hold)
    return pending, hold


def test_actual_conflict_keeps_copy_and_edits_made_during_save(tmp_path, studio_browser):
    pytest.importorskip('playwright.sync_api')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.root = tmp_path / 'traind'
    try:
        with studio_browser() as page:
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            add(page, 'Prompt template')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('.studio-status')).to_contain_text('Saved')
            other = page.context.new_page()
            other.goto(page.url)
            other.locator('.studio-node').focus()
            other.keyboard.press('Enter')
            page.locator('.studio-inspector').get_by_label('template', exact=True).fill('Remote latest')
            page.locator('.studio-inspector').get_by_label('template', exact=True).press('Tab')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('.studio-status')).to_contain_text('Saved')
            pending, hold = hold_next_save(other)
            other.locator('.studio-inspector').get_by_label('template', exact=True).fill('Local first')
            other.locator('.studio-inspector').get_by_label('template', exact=True).press('Tab')
            with other.expect_request('**/ui/studio/workflows/*'):
                other.get_by_role('button', name='Save', exact=True).click()
            other.wait_for_function("() => window.studioSaveHeld === true")
            assert pending
            other.locator('.studio-inspector').get_by_label('template', exact=True).fill('Local newer')
            other.locator('.studio-inspector').get_by_label('template', exact=True).press('Tab')
            pending[0].fulfill(response=pending[0].fetch())
            expect(other.locator('.studio-status')).to_contain_text('Saved')
            other.unroute('**/ui/studio/workflows/*', hold)
            expect(other.locator('#studio-workflows option')).to_have_count(3)
            selected = other.get_by_label('Saved workflows').input_value()
            other.reload()
            other.get_by_label('Saved workflows').select_option(selected)
            other.locator('.studio-node').focus()
            other.keyboard.press('Enter')
            expect(other.locator('.studio-inspector').get_by_label('template', exact=True)).to_have_value('Local newer')
            other.close()
    finally:
        if hasattr(served.ui, 'studio'):
            served.ui.studio.close()
        served.close()


def test_actual_new_waits_for_pending_workflow_save(tmp_path, studio_browser):
    pytest.importorskip('playwright.sync_api')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.root = tmp_path / 'traind'
    try:
        with studio_browser() as page:
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            add(page, 'Prompt template')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('.studio-status')).to_contain_text('Saved')
            original = page.get_by_label('Saved workflows').input_value()
            pending, hold = hold_next_save(page)
            page.locator('.studio-inspector').get_by_label('template', exact=True).fill('Original pending edit')
            page.locator('.studio-inspector').get_by_label('template', exact=True).press('Tab')
            page.get_by_role('button', name='Save', exact=True).click()
            page.wait_for_function("() => window.studioSaveHeld === true")
            page.get_by_role('button', name='New', exact=True).click()
            expect(page.locator('.studio-node')).to_have_count(1)
            pending[0].fulfill(response=pending[0].fetch())
            expect(page.locator('.studio-node')).to_have_count(0)
            page.unroute('**/ui/studio/workflows/*', hold)
            add(page, 'Prompt template')
            page.locator('.studio-inspector').get_by_label('template', exact=True).fill('New workflow text')
            page.locator('.studio-inspector').get_by_label('template', exact=True).press('Tab')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('.studio-status')).to_contain_text('Saved')
            assert page.get_by_label('Saved workflows').input_value() != original
            page.get_by_label('Saved workflows').select_option(original)
            page.locator('.studio-node').focus()
            page.keyboard.press('Enter')
            expect(page.locator('.studio-inspector').get_by_label('template', exact=True)).to_have_value('Original pending edit')
    finally:
        if hasattr(served.ui, 'studio'):
            served.ui.studio.close()
        served.close()


def test_failed_save_blocks_run_and_new_workflow(studio_page):
    page, state, errors = studio_page
    add(page, 'Prompt template')
    state['save_error'] = 'Save unavailable'
    page.get_by_role('button', name='Save', exact=True).click()
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('.studio-status')).to_have_text('Save unavailable')
    assert not state['submissions']
    page.get_by_role('button', name='New', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(1)
    del state['save_error']
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('#studio-runs')).to_contain_text('done')
    assert len(state['submissions']) == 1
    assert not errors


def test_subtype_port_suggestions_and_inspector_connections(studio_page):
    page, _state, errors = studio_page
    add(page, 'Language producer')
    page.get_by_role('button', name='Language producer output model, language-model').click()
    expect(page.locator('.studio-library')).to_contain_text('Compatible nodes')
    expect(page.locator('.studio-library').get_by_role('button', name='Model consumer')).to_be_visible()
    expect(page.locator('.studio-library').get_by_role('button', name='Report')).to_have_count(0)
    add(page, 'Model consumer')
    expect(page.locator('.studio-connections path')).to_have_count(1)
    expect(page.locator('.studio-inspector').get_by_label('Connect model')).not_to_have_value('')
    assert not errors


def test_actual_sweep_controls_save_typed_choices(tmp_path, studio_browser):
    pytest.importorskip('playwright.sync_api')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.root = tmp_path / 'traind'
    try:
        with studio_browser() as page:
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            page.locator('.studio-library').get_by_role('button', name='Inference Ready to configure', exact=True).click()
            page.locator('.studio-node[aria-label="Generate"]').focus()
            page.keyboard.press('Enter')
            page.locator('.studio-inspector').get_by_text('Sweep parameters', exact=True).click()
            page.get_by_label('Sweep temperature', exact=True).fill('0.1\n0.8')
            page.get_by_label('Sweep temperature', exact=True).press('Tab')
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('.studio-status')).to_contain_text('Saved')
            page.reload()
            page.locator('.studio-node[aria-label="Generate"]').focus()
            page.keyboard.press('Enter')
            page.locator('.studio-inspector').get_by_text('Sweep parameters', exact=True).click()
            expect(page.get_by_label('Sweep temperature', exact=True)).to_have_value('0.1\n0.8')
    finally:
        if hasattr(served.ui, 'studio'):
            served.ui.studio.close()
        served.close()


def seed_report_artifact(served, source_id):
    engine = served.ui.studio
    source = engine.store.get('run', source_id, studio_service.scope(served.ui))
    assert source['state'] == 'done'
    ident = uuid.uuid4().hex
    node = {'id': 'report', 'type': 'report', 'version': 1, 'parameters': {}, 'bindings': {}}
    folder = served.files / 'studio' / ident / hashlib.sha256(b'report').hexdigest()[:24]
    folder.mkdir(parents=True)
    (folder / 'report.json').write_text('{"score":1.0}')
    engine.store.put('run', {
        'id': ident, 'title': 'Report artifact', 'scope': source['scope'], 'principal': source['principal'],
        'workflow_id': source['workflow_id'], 'revision': source['revision'], 'state': 'done',
        'graph': {'nodes': [node], 'edges': [], 'groups': {}}, 'created_at': source['created_at'] + 1,
        'nodes': {'report': {'state': 'done', 'outputs': {
            'artifact': {'kind': 'artifact', 'path': str(folder / 'report.json')}}}},
    })


def test_actual_run_logs_and_declared_artifact_download(tmp_path, studio_browser):
    pytest.importorskip('playwright.sync_api')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.root = tmp_path / 'traind'
    try:
        with studio_browser() as page:
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            add(page, 'Prompt template')
            with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/ui/studio/runs')) as submitted:
                page.get_by_role('button', name='Run workflow', exact=True).click()
            source_id = submitted.value.json()['run']['id']
            expect(page.locator('#studio-runs')).to_contain_text('done')
            page.get_by_role('tab', name='Runs and Results', exact=True).click()
            seed_report_artifact(served, source_id)
            page.get_by_role('button', name='Refresh', exact=True).click()
            page.get_by_role('button', name='Report artifact', exact=True).click()
            page.get_by_role('button', name='View logs · report', exact=True).click()
            expect(page.locator('#studio-run-detail')).to_contain_text('No worker log for this node.')
            page.get_by_role('button', name='Show artifact files', exact=True).click()
            with page.expect_download() as download:
                page.get_by_role('button', name='Download report.json', exact=True).click()
            assert json.loads(download.value.path().read_text()) == {'score': 1.0}
    finally:
        if hasattr(served.ui, 'studio'):
            served.ui.studio.close()
        served.close()


@pytest.mark.parametrize(('field', 'invalid', 'valid'), [
    ('iterations', 'not-a-number', '2'), ('mode', 'other', 'train'), ('enabled', 'maybe', 'true'),
])
def test_invalid_sweep_draft_blocks_submission_and_survives_selection(studio_page, field, invalid, valid):
    page, state, errors = studio_page
    add(page, 'Report')
    page.locator('.studio-inspector').get_by_text('Sweep parameters', exact=True).click()
    page.get_by_label('Sweep ' + field, exact=True).fill(invalid)
    page.get_by_label('Sweep ' + field, exact=True).press('Tab')
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Correct the highlighted sweep value')
    assert not state['submissions']
    add(page, 'Model consumer')
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.get_by_label('Sweep ' + field, exact=True)).to_have_value(invalid)
    assert not state['saved']
    assert not state['submissions']
    page.get_by_label('Sweep ' + field, exact=True).fill(valid)
    page.get_by_label('Sweep ' + field, exact=True).press('Tab')
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('#studio-runs')).to_contain_text('done')
    assert len(state['submissions']) == 1
    assert not errors


def test_managed_browser_contexts_do_not_share_state(studio_browser):
    with studio_browser() as first:
        first.route('http://studio.invalid/**', lambda route: route.fulfill(body='<p>isolated</p>'))
        first.goto('http://studio.invalid/')
        first.context.add_cookies([{'name': 'session', 'value': 'private', 'url': 'http://studio.invalid/'}])
        first.evaluate("localStorage.setItem('studio', 'private'); sessionStorage.setItem('studio', 'private')")
    with studio_browser() as second:
        second.route('http://studio.invalid/**', lambda route: route.fulfill(body='<p>isolated</p>'))
        second.goto('http://studio.invalid/')
        assert second.context.cookies() == []
        assert second.evaluate("[localStorage.getItem('studio'), sessionStorage.getItem('studio')]") == [None, None]

"""Studio graph editing and durable run controls through the application."""
from __future__ import annotations

import pytest
from browser_expect import expect
from test_fleet_ui import Serving

from poolhouse.scrape.browser import Window, browser

pytestmark = pytest.mark.slow

SPECS = [
    {'type': 'prompt', 'version': 1, 'title': 'Prompt template', 'family': 'Inference',
     'inputs': [], 'outputs': [{'name': 'text', 'type': 'text'}],
     'parameters': {'properties': {'template': {'type': 'string', 'default': 'Hello', 'title': 'Prompt text'}}}},
    {'type': 'report', 'version': 1, 'title': 'Report', 'family': 'Results',
     'inputs': [{'name': 'text', 'type': 'text'}], 'outputs': [],
     'parameters': {'properties': {'title': {'type': 'string', 'default': 'Results'}}}},
]


@pytest.fixture
def studio_page(tmp_path):
    pytest.importorskip('playwright.sync_api')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    state = {'workflows': [], 'runs': [], 'submissions': [], 'saved': []}
    with browser(Window(profile=tmp_path / 'browser', headless=True)) as page:
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
                       'state': 'completed', 'nodes': {}}
                state['runs'] = [run]
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
    page.locator('.studio-library').get_by_role('button', name=title).click()


def test_build_connect_configure_run_and_snapshot(studio_page):
    page, state, errors = studio_page
    add(page, 'Prompt template')
    add(page, 'Report')
    page.locator('.studio-inspector').get_by_label('Connect text').select_option(index=1)
    expect(page.locator('.studio-connections path')).to_have_count(1)
    page.get_by_label('Workflow title').fill('Experiment')
    page.get_by_label('Workflow title').press('Tab')
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('#studio-runs')).to_contain_text('completed')
    assert len(state['submissions']) == 1
    assert state['saved'][-1]['graph']['edges'][0]['target']['port'] == 'text'
    first = state['submissions'][0]
    page.get_by_role('button', name='Run workflow', exact=True).click()
    page.wait_for_function("document.querySelector('.studio-status').textContent.includes('submitted')")
    assert state['submissions'][-1]['idempotency_key'] == first['idempotency_key']
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
    page.locator('.studio-inspector').get_by_label('Prompt text').fill(value)
    page.locator('.studio-inspector').get_by_label('Prompt text').press('Tab')
    page.get_by_role('button', name='Share', exact=True).click()
    expect(page.locator('#studio-portable')).to_contain_text(value)
    assert page.locator('#studio-portable img').count() == 0
    assert not page.evaluate('Boolean(window.studioInjected)')
    assert not errors

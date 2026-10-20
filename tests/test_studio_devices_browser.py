"""Device choices and dispatch feedback through Studio controls."""

from __future__ import annotations

import json

import pytest
from browser_expect import expect
from studio_commands import studio_command
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401

pytestmark = pytest.mark.slow


@pytest.fixture
def device_page(tmp_path, request):
    open_page = request.getfixturevalue('studio_browser')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.root = tmp_path / 'traind'
    devices = [
        {'id': 'local-device', 'name': 'This workstation', 'is_self': True,
         'available': True, 'capabilities': ['cpu', 'inference']},
        {'id': 'remote-device', 'name': 'Pool worker', 'is_self': False,
         'available': True, 'capabilities': ['cpu', 'training']},
        {'id': 'offline-device', 'name': 'Unavailable worker', 'is_self': False,
         'available': False, 'capabilities': []},
    ]
    try:
        with open_page() as page:
            page.set_viewport_size({'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/ui/studio/devices', lambda route: route.fulfill(json={'devices': devices}))
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            page.locator('.studio-library').get_by_role('button', name='Inference Ready to configure', exact=True).click()
            page.locator('.studio-node[aria-label="Generate"]').focus()
            page.keyboard.press('Enter')
            yield page, devices, errors
    finally:
        if hasattr(served.ui, 'studio'):
            served.ui.studio.close()
        served.close()


def test_device_selection_saves_and_is_excluded_from_package(device_page):
    page, _devices, errors = device_page
    select = page.get_by_label('Execution device', exact=True)
    expect(select).to_have_value('local-device')
    expect(select.locator('option[value="offline-device"]')).to_be_disabled()
    select.select_option('remote-device')
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Saved')
    page.reload()
    page.locator('.studio-node[aria-label="Generate"]').focus()
    page.keyboard.press('Enter')
    expect(page.get_by_label('Execution device', exact=True)).to_have_value('remote-device')
    expect(page.locator('.studio-node[aria-label="Generate"]')).to_contain_text('Device: Pool worker')
    studio_command(page, 'Share').click()
    page.get_by_label('Package content').select_option('selection')
    with page.expect_download() as download:
        page.get_by_role('button', name='Export file', exact=True).click()
    package = json.loads(download.value.path().read_text())
    assert package['kind'] == 'group'
    assert {node['type'] for node in package['graph']['nodes']} == {'model', 'prompt', 'generate'}
    assert len(package['graph']['edges']) == 2
    assert all('execution' not in node for node in package['graph']['nodes'])
    assert not errors


def test_missing_target_is_retained_until_explicit_reselection(device_page):
    page, devices, errors = device_page
    page.get_by_label('Execution device', exact=True).select_option('remote-device')
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Saved')
    devices[:] = [device for device in devices if device['id'] != 'remote-device']
    page.reload()
    page.locator('.studio-node[aria-label="Generate"]').focus()
    page.keyboard.press('Enter')
    select = page.get_by_label('Execution device', exact=True)
    expect(select).to_have_value('remote-device')
    expect(select.locator('option[value="remote-device"]')).to_be_disabled()
    expect(page.locator('.studio-node[aria-label="Generate"]')).to_contain_text('Selected device is unavailable.')
    select.select_option('local-device')
    expect(page.locator('.studio-node[aria-label="Generate"]')).not_to_contain_text('Selected device is unavailable.')
    assert not errors


def test_local_inputs_have_no_remote_target_control(device_page):
    page, _devices, errors = device_page
    page.locator('.studio-node[aria-label="Model"]').focus()
    page.keyboard.press('Enter')
    expect(page.get_by_label('Execution device', exact=True)).to_have_count(0)
    page.locator('.studio-node[aria-label="Prompt template"]').focus()
    page.keyboard.press('Enter')
    expect(page.get_by_label('Execution device', exact=True)).to_have_count(0)
    assert not errors


def test_preflight_error_marks_affected_node_without_dispatch(device_page):
    page, _devices, errors = device_page
    submitted = []
    page.route('**/ui/studio/validate', lambda route: route.fulfill(json={
        'valid': False, 'errors': [{'node': 'generate', 'code': 'dependency',
                                  'message': 'The selected device needs its managed runtime.'}],
    }))
    page.on('request', lambda request: submitted.append(request)
            if request.url.endswith('/ui/studio/runs') and request.method == 'POST' else None)
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('.studio-node[aria-label="Generate"]')).to_contain_text('Needs setup')
    expect(page.locator('.studio-inspector')).to_contain_text('The selected device needs its managed runtime.')
    assert not submitted
    assert not errors


def test_actual_device_discovery_persists_authenticated_selection(tmp_path, request):
    open_page = request.getfixturevalue('studio_browser')
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.root = tmp_path / 'traind'
    try:
        with open_page() as page:
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            with page.expect_response(lambda response: response.url.endswith('/ui/studio/devices')) as discovered:
                page.goto(f'http://127.0.0.1:{served.port}/ui/')
            assert discovered.value.status == 200
            devices = discovered.value.json()['devices']
            local = next(device for device in devices if device['is_self'])
            page.locator('.studio-library').get_by_role('button', name='Inference Ready to configure', exact=True).click()
            page.locator('.studio-node[aria-label="Generate"]').focus()
            page.keyboard.press('Enter')
            select = page.get_by_label('Execution device', exact=True)
            expect(select).to_have_value(local['id'])
            select.select_option(local['id'])
            page.get_by_role('button', name='Save', exact=True).click()
            expect(page.locator('.studio-status')).to_contain_text('Saved')
            page.reload()
            page.locator('.studio-node[aria-label="Generate"]').focus()
            page.keyboard.press('Enter')
            expect(page.get_by_label('Execution device', exact=True)).to_have_value(local['id'])
            assert page.locator('studio-view').evaluate(
                "editor => editor.graph.nodes.find(node => node.id === 'generate').execution") == {'machine': local['id']}
            assert not errors
    finally:
        if hasattr(served.ui, 'studio'):
            served.ui.studio.close()
        served.close()

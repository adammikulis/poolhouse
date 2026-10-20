"""Explicit fixed model setup through the application and actual durable setup jobs."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_decision_browser import choose_template, focus_node

from poolhouse.fleet import studio_decision_setup as setup
from poolhouse.studio import decision_inventory

pytestmark = pytest.mark.slow


@pytest.fixture
def configure_page(tmp_path, request, monkeypatch):
    open_page = request.getfixturevalue('studio_browser')
    state = {'libraries': False, 'pins': 0, 'installs': 0, 'failure': False, 'posts': []}
    started, release = threading.Event(), threading.Event()
    def install(names, *, on_progress):
        assert names == ['core', 'decide-pointer']
        state['installs'] += 1
        started.set()
        assert release.wait(10)
        if state['failure']:
            return {'core': {'ok': False, 'error': 'Unable to verify the model setup'}}
        state['libraries'] = True
        return {name: {'ok': True} for name in names}
    environment = SimpleNamespace(exists=True, require_current_runtime=lambda: None,
                                  require_compute=lambda: 'verified',
                                  installed=lambda: dict.fromkeys(setup.REQUIRED, '1') if state['libraries'] else {},
                                  install=install)
    def resource():
        if state['pins'] != len(setup.STRANDS_V19.files):
            raise ValueError('Missing fixed model files')
        return {'id': setup.RESOURCE_ID}
    def locate(pin, *, download, endpoint):
        assert pin in setup.STRANDS_V19.files and download and endpoint == setup.ENDPOINT
        state['pins'] += 1
    monkeypatch.setattr(setup, 'resource', resource)
    monkeypatch.setattr(decision_inventory, 'resource', resource)
    monkeypatch.setattr(setup, 'locate', locate)
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.settings.download_sources = 'both'
    served.ui.root = tmp_path / 'traind'
    served.ui.environment = served.runner.environment = environment
    try:
        with open_page() as page:
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda req: state['posts'].append(req.post_data_json) if req.method == 'POST' and req.url.endswith('/ui/studio/resources/decision/install') else None)
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            choose_template(page, 'Prompt injection check')
            focus_node(page, 'model')
            yield page, state, started, release, errors, served
    finally:
        release.set()
        if served.ui.setup_jobs and (worker := served.ui.setup_jobs._worker):
            worker.join(5)
            assert not worker.is_alive()
        if hasattr(served.ui, 'studio'):
            served.ui.studio.close()
        served.close()


def test_explicit_configure_reconnects_to_one_job_and_verifies_ready(configure_page):
    page, state, started, release, errors, served = configure_page
    assert state['posts'] == [] and state['installs'] == 0
    button = page.locator('.studio-inspector').get_by_role('button', name='Configure decision model', exact=True)
    button.click()
    assert started.wait(5)
    expect(page.locator('.studio-inspector').get_by_role('button', name='Configuring decision model…', exact=True)).to_be_disabled()
    assert state['posts'] == [{}]
    assert state['installs'] == 1
    page.reload()
    focus_node(page, 'model')
    expect(page.get_by_label('Choose model', exact=True)).to_have_value(setup.RESOURCE_ID)
    expect(page.locator('.studio-inspector').get_by_role('button', name='Configuring decision model…', exact=True)).to_be_disabled()
    assert state['posts'] == [{}]
    release.set()
    expect(page.locator('.studio-inspector')).to_contain_text('Decision model ready.', timeout=10000)
    assert state['pins'] == len(setup.STRANDS_V19.files)
    assert len(served.ui.setup_jobs.all()) == 1
    assert state['posts'] == [{}]
    assert not errors


def test_failed_setup_shows_error_without_automatic_retry(configure_page):
    page, state, _started, release, errors, served = configure_page
    state['failure'] = True
    release.set()
    page.locator('.studio-inspector').get_by_role('button', name='Configure decision model', exact=True).click()
    expect(page.locator('.studio-inspector')).to_contain_text('Unable to verify the model setup', timeout=10000)
    assert state['installs'] == 1 and state['posts'] == [{}]
    assert served.ui.setup_jobs.all()[0]['state'] == 'failed'
    page.reload()
    focus_node(page, 'model')
    expect(page.locator('.studio-inspector')).to_contain_text('Unable to verify the model setup')
    assert state['installs'] == 1 and state['posts'] == [{}]
    state['failure'] = False
    page.locator('.studio-inspector').get_by_role('button', name='Configure decision model', exact=True).click()
    expect(page.locator('.studio-inspector')).to_contain_text('Decision model ready.', timeout=10000)
    assert state['installs'] == 2 and state['posts'] == [{}, {}]
    assert not errors


def test_narrow_screen_configure_is_keyboard_accessible(configure_page):
    page, state, _started, release, errors, _served = configure_page
    page.set_viewport_size({'width': 390, 'height': 844})
    button = page.locator('.studio-inspector').get_by_role('button', name='Configure decision model', exact=True)
    expect(button).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    release.set()
    button.focus()
    page.keyboard.press('Enter')
    expect(page.locator('.studio-inspector')).to_contain_text('Decision model ready.', timeout=10000)
    assert state['posts'] == [{}]
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors


def test_keyboard_node_focus_survives_background_setup_refresh(configure_page):
    page, _state, _started, _release, errors, _served = configure_page
    card = page.locator('.studio-node[data-node-id="model"]')
    card.focus()
    page.locator('studio-view').evaluate('editor => editor.render()')
    expect(card).to_be_focused()
    page.keyboard.press('Enter')
    expect(page.locator('.studio-inspector').get_by_label('Choose model', exact=True)).to_be_visible()
    assert not errors

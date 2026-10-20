"""Managed watch selection uses authenticated one-folder browsing and explicit exact-file grants."""
from __future__ import annotations

import pytest
from browser_expect import expect
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_live_browser import live_page as _live_page, open_live

live_page = _live_page
pytestmark = pytest.mark.slow


def upload_default(page, app):
    page.get_by_role('navigation', name='Workspace pages').get_by_role('link', name='Data', exact=True).click()
    page.get_by_role('button', name='+ Add dataset', exact=True).click()
    page.get_by_role('button', name='Choose a file', exact=True).click()
    page.get_by_label('Choose a text / JSONL file', exact=True).set_input_files({
        'name': 'watch-records.jsonl', 'mimeType': 'application/x-ndjson', 'buffer': b'{"record":"selected"}\n'})
    expect(page.get_by_label('Destination relative to files root', exact=True)).to_have_value('datasets/watch-records.jsonl')
    page.get_by_role('button', name='Upload', exact=True).click()
    expect(page.locator('data-view .status')).to_contain_text('Dataset uploaded.')
    page.get_by_role('navigation', name='Workspace pages').get_by_role('link', name='Canvas', exact=True).click()


def uploaded_picker(page):
    open_live(page)
    page.locator('.studio-live').get_by_text('Watch a file or folder', exact=True).click()
    return page.get_by_label('Uploaded file or folder', exact=True)


def test_default_data_upload_browses_nested_file_and_registers_only_that_source(live_page):
    page, app, errors, posts = live_page
    upload_default(page, app)
    (app.files / 'datasets' / 'unrelated.jsonl').write_text('{"record":"unrelated"}\n')
    picker = uploaded_picker(page)
    picker.select_option('datasets')
    page.get_by_role('button', name='Open selected folder', exact=True).click()
    picker.select_option('datasets/watch-records.jsonl')
    page.get_by_role('button', name='Watch uploaded copy', exact=True).click()
    expect(page.locator('.studio-live')).to_contain_text('Watches the uploaded managed copy.')
    registrations = [body for url, body in posts if url.endswith('/sources')]
    assert len(registrations) == 1
    assert registrations[0]['resource_id'] == 'datasets/watch-records.jsonl'
    selection = page.locator('studio-view').evaluate('editor => ({source: editor.liveSource.value, sources: editor.liveSources.map(s => s.id), input: editor.liveInput.value, nodes: editor.rootGraph().nodes.map(n => n.id)})')
    assert selection['source'] in selection['sources'] and selection['input'], selection
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.get_by_role('button', name='Start stream', exact=True)).to_be_enabled()
    assert app.ui.studio_streams.store.active() == []
    assert not any(url.endswith('/start') for url, _ in posts)
    assert not errors


def test_nested_picker_keyboard_narrow_navigation_and_safe_filename(live_page):
    page, app, errors, posts = live_page
    folder = app.files / 'datasets'
    folder.mkdir()
    name = '<img src=x onerror=window.injected=true>.jsonl'
    (folder / name).write_text('{}\n')
    page.set_viewport_size({'width': 390, 'height': 844})
    picker = uploaded_picker(page)
    picker.select_option('datasets')
    opener = page.get_by_role('button', name='Open selected folder', exact=True)
    opener.focus()
    page.keyboard.press('Enter')
    expect(picker).to_be_focused()
    assert picker.locator('option').filter(has_text=name).count() == 1
    (folder / 'safe.jsonl').write_text('{}\n')
    page.get_by_role('button', name='Refresh uploaded files', exact=True).click()
    picker.select_option('datasets/safe.jsonl')
    page.get_by_role('button', name='Watch uploaded copy', exact=True).focus()
    page.keyboard.press('Enter')
    expect(page.locator('.studio-live')).to_contain_text('Watches the uploaded managed copy.')
    page.get_by_role('navigation', name='Uploaded folder navigation').get_by_role('button', name='Uploaded files', exact=True).click()
    expect(picker).to_be_focused()
    expect(picker.locator('option[value="datasets"]')).to_have_count(1)
    assert page.evaluate('window.injected') is None
    assert page.locator('.studio-live img').count() == 0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert len([body for url, body in posts if url.endswith('/sources')]) == 1
    assert not errors


@pytest.mark.parametrize('change', ['hide', 'workflow', 'revoke'])
@pytest.mark.parametrize('status', [200, 403])
def test_late_folder_response_cannot_restore_selection_in_changed_context(live_page, change, status):
    page, app, errors, posts = live_page
    folder = app.files / 'datasets'
    folder.mkdir()
    (folder / 'delayed.jsonl').write_text('{}\n')
    picker = uploaded_picker(page)
    def hold(route):
        response = route.fetch()
        if change == 'hide':
            page.locator('studio-view').evaluate('editor => { editor.liveBox.hidden = true; editor.stopLive(); }')
        elif change == 'workflow':
            page.locator('studio-view').evaluate('editor => editor.open(null)')
        else:
            page.locator('studio-view').evaluate("editor => editor.liveError(Object.assign(Error('Sign in'), {response: {status: 403}}))")
        if status == 200:
            route.fulfill(response=response)
        else:
            route.fulfill(status=status, json={'error': 'Old request refused'})
    page.route('**/ui/workspace/files?path=datasets', hold)
    page.locator('studio-view').evaluate("editor => editor.browseManaged('datasets')")
    expect(picker.locator('option[value="datasets/delayed.jsonl"]')).to_have_count(0)
    expect(page.locator('.studio-live').get_by_role('button', name='Watch uploaded copy', exact=True, include_hidden=True)).to_be_disabled()
    assert not any(url.endswith('/sources') for url, _ in posts)
    assert not errors


@pytest.mark.parametrize('phase', ['post-workflow', 'post-source', 'post-error', 'refresh-source', 'refresh-error-workflow', 'refresh-error-current'])
def test_watch_ack_and_refresh_never_adopt_a_source_into_changed_context(live_page, phase):
    page, app, errors, posts = live_page
    (app.files / 'events.jsonl').write_text('{}\n')
    picker = uploaded_picker(page)
    picker.select_option('events.jsonl')
    other = page.get_by_label('Live source', exact=True).locator('option').nth(1).get_attribute('value')
    changed = []
    def change_context():
        changed.append(True)
        if phase in {'post-source', 'refresh-source'}:
            page.get_by_label('Live source', exact=True).select_option(other)
        else:
            page.get_by_role('button', name='New', exact=True).click()
            page.locator('studio-view').evaluate("editor => { editor.liveNote.textContent = 'Current workflow'; }")
    def register(route):
        if phase == 'post-error':
            change_context()
            route.fulfill(status=409, json={'error': 'Old registration refused'})
            return
        response = route.fetch()
        if not phase.startswith('refresh'):
            change_context()
        route.fulfill(response=response)
    def inventory(route):
        response = route.fetch()
        if phase != 'refresh-error-current':
            change_context()
        if phase.startswith('refresh-error'):
            route.fulfill(status=503, json={'error': 'Refresh unavailable'})
        else:
            route.fulfill(response=response)
    page.route('**/ui/studio/sources', lambda route: register(route) if route.request.method == 'POST'
               else inventory(route) if phase.startswith('refresh') else route.continue_())
    page.get_by_role('button', name='Watch uploaded copy', exact=True).click()
    page.wait_for_function("() => !document.querySelector('studio-view').liveBusy")
    assert changed or phase == 'refresh-error-current'
    if phase in {'post-source', 'refresh-source'}:
        expect(page.get_by_label('Live source', exact=True)).to_have_value(other)
    elif phase == 'refresh-error-current':
        expect(page.locator('.studio-live [role="status"]')).to_have_text('Refresh unavailable')
    else:
        expect(page.get_by_label('Workflow title')).to_have_value('Untitled experiment')
        expect(page.locator('.studio-live [role="status"]')).to_have_text('Current workflow')
    assert not any(url.endswith('/start') or url.endswith('/subscriptions') for url, _ in posts)
    assert not errors


@pytest.mark.parametrize('status', [200, 503])
def test_live_sources_wait_for_successful_current_workflow_load(live_page, status):
    page, _app, errors, posts = live_page
    checked = []
    def resources(route):
        response = route.fetch()
        expect(page.get_by_role('button', name='Live sources', exact=True)).to_be_disabled()
        checked.append(True)
        if status == 200:
            route.fulfill(response=response)
        else:
            route.fulfill(status=503, json={'error': 'Resources unavailable'})
    page.route('**/ui/studio/resources', resources)
    page.reload()
    if status == 200:
        open_live(page)
        expect(page.get_by_label('Live record input')).not_to_have_value('')
    else:
        expect(page.locator('.studio-status')).to_contain_text('Resources unavailable')
        expect(page.get_by_role('button', name='Live sources', exact=True)).to_be_disabled()
    assert checked
    assert not any(url.endswith('/sources') or url.endswith('/start') for url, _ in posts)
    assert not errors


def test_late_workflow_load_cannot_enable_live_on_another_page(live_page):
    page, _app, errors, _posts = live_page
    held = []
    def resources(route):
        if held:
            route.continue_()
            return
        response = route.fetch()
        held.append(True)
        page.get_by_role('navigation', name='Workspace pages').get_by_role('link', name='Data', exact=True).click()
        route.fulfill(response=response)
    page.route('**/ui/studio/resources', resources)
    page.reload()
    page.wait_for_function("() => !document.querySelector('studio-view').loading")
    expect(page.get_by_role('button', name='Live sources', exact=True, include_hidden=True)).to_be_disabled()
    page.get_by_role('navigation', name='Workspace pages').get_by_role('link', name='Canvas', exact=True).click()
    open_live(page)
    expect(page.get_by_label('Live record input')).not_to_have_value('')
    assert held and not errors

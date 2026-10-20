"""Exact uploaded dataset selection through authenticated one-folder browsing."""
from __future__ import annotations

import re

import pytest
from browser_expect import expect
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_live_browser import live_page as _live_page
from test_studio_managed_browser import upload_default

live_page = _live_page
pytestmark = pytest.mark.slow


def dataset_node(page):
    page.get_by_label('Search nodes, templates and groups').fill('Dataset')
    page.locator('.studio-library').get_by_role('button', name=re.compile('^Dataset')).click()
    picker = page.locator('.studio-inspector').get_by_label('Choose dataset', exact=True)
    expect(picker).to_be_enabled()
    return picker


def binding(page):
    return page.locator('studio-view').evaluate('editor => editor.graph.nodes.find(n => n.type === "dataset").bindings')


def uploaded_nested_dataset_binds_only_selected_file_with_keyboard_and_narrow_theme(live_page):
    page, app, errors, posts = live_page
    upload_default(page, app)
    (app.files / 'datasets' / 'unrelated.jsonl').write_text('{}\n')
    (app.files / 'datasets' / '<img src=x onerror=window.injected=true>.jsonl').write_text('{}\n')
    requests = []
    page.on('request', lambda request: requests.append(request.url) if '/ui/workspace/files?' in request.url else None)
    page.set_viewport_size({'width': 390, 'height': 844})
    picker = dataset_node(page)
    picker.select_option('datasets')
    assert binding(page) == {}
    opener = page.get_by_role('button', name='Open selected dataset folder', exact=True)
    opener.focus()
    page.keyboard.press('Enter')
    expect(picker.locator('option[value="datasets/watch-records.jsonl"]')).to_have_count(1)
    expect(picker).to_be_focused()
    assert picker.locator('option').filter(has_text='<img src=x').count() == 1
    assert page.locator('.studio-inspector img').count() == 0
    assert page.evaluate('window.injected') is None
    picker.select_option('datasets/watch-records.jsonl')
    expect(page.locator('.studio-inspector')).to_contain_text('Selected: datasets/watch-records.jsonl')
    assert binding(page) == {'dataset': {'kind': 'dataset', 'id': 'datasets/watch-records.jsonl'}}
    for theme in ('dark', 'light'):
        page.evaluate('theme => document.documentElement.dataset.theme = theme', theme)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert all('path=' in url and not url.endswith('path=datasets%2Fwatch-records.jsonl') for url in requests)
    page.get_by_role('button', name='Clear dataset', exact=True).click()
    assert binding(page) == {}
    assert not any(url.endswith('/sources') for url, _ in posts)
    assert not errors


def delayed_binding_response(page, change, status):
    picker = page.locator('.studio-inspector').get_by_label('Choose dataset', exact=True)
    expect(picker).to_be_enabled()
    page.evaluate('window.bindingResponseDelivered = false')
    if change == 'binding':
        page.locator('studio-view').evaluate('editor => { editor.graph.nodes.find(n => n.type === "dataset").bindings.dataset = {kind:"dataset",id:"before.jsonl"}; editor.inspector(); }')
        expect(picker).to_be_enabled()
    picker.select_option('datasets')
    changes = {
        'root': 'editor.graph = structuredClone(editor.graph)',
        'group': 'editor.context = [{node: {id: "new-group"}}]',
        'node': 'editor.selected.clear()',
        'node-id': 'const node = editor.graph.nodes.find(n => n.type === "dataset"); node.id = "replacement-id"; editor.selected = new Set([node.id])',
        'binding': 'editor.graph.nodes.find(n => n.type === "dataset").bindings.dataset.id = "different.jsonl"',
        'edit': 'editor.editSerial++',
        'disconnect': 'editor.querySelector(".studio-dataset-binding").remove()',
    }
    handled = False
    def hold(route):
        nonlocal handled
        if handled:
            route.continue_()
            return
        handled = True
        response = route.fetch()
        if change == 'workflow':
            page.locator('studio-view').evaluate('editor => editor.workflowId = "different-workflow"')
        elif change == 'route':
            page.evaluate('location.hash = "data"')
            expect(page.locator('studio-view > section')).to_be_hidden()
        else:
            page.locator('studio-view').evaluate('editor => {' + changes[change] + '}')
        if status == 200:
            route.fulfill(response=response)
        else:
            route.fulfill(status=403, json={'error': 'Stale binding refusal'})
        page.evaluate('window.bindingResponseDelivered = true')
    page.route('**/ui/workspace/files?path=datasets', hold)
    page.get_by_role('button', name='Open selected dataset folder', exact=True).click()
    page.wait_for_function('() => window.bindingResponseDelivered === true')
    page.wait_for_function('''() => [...document.querySelectorAll('.studio-dataset-binding option')]
        .every(option => option.value !== 'datasets/delayed.jsonl')''')
    assert 'Stale binding refusal' not in page.locator('body').inner_text()
    if change != 'binding':
        assert not binding(page).get('dataset')
    else:
        assert binding(page)['dataset']['id'] == 'different.jsonl'
    page.unroute('**/ui/workspace/files?path=datasets', hold)


def late_binding_listing_cannot_mutate_changed_editor_or_publish_old_error(live_page):
    page, app, errors, _posts = live_page
    folder = app.files / 'datasets'
    folder.mkdir(exist_ok=True)
    (folder / 'delayed.jsonl').write_text('{}\n')
    dataset_node(page)
    page.get_by_role('button', name='Save', exact=True).click()
    page.wait_for_function('() => { const editor = document.querySelector("studio-view"); return !editor.dirty && !editor.saving; }')
    saved = page.locator('studio-view').evaluate('editor => ({graph: structuredClone(editor.graph), workflow: editor.workflowId})')
    for status in (200, 403):
        for change in ('workflow', 'root', 'group', 'node', 'node-id', 'binding', 'edit', 'route', 'disconnect'):
            delayed_binding_response(page, change, status)
            if change == 'route':
                page.get_by_role('navigation', name='Workspace pages').get_by_role('link', name='Canvas', exact=True).click()
            page.locator('studio-view').evaluate('''async (editor, saved) => {
                editor.context = []; editor.workflowId = saved.workflow;
                editor.graph = structuredClone(saved.graph);
                editor.selected = new Set(editor.graph.nodes.filter(n => n.type === 'dataset').map(n => n.id));
                await editor.load();
            }''', saved)
    assert not errors



def picker_rejects_unlisted_paths_folders_and_malformed_rows(live_page):
    page, _app, errors, _posts = live_page
    page.route('**/ui/workspace/files?path=', lambda route: route.fulfill(json={
        'ok': True, 'path': '', 'files': [
            {'name': 'safe.jsonl', 'path': 'safe.jsonl', 'directory': False},
            {'name': 'wrong.jsonl', 'path': 'nested/wrong.jsonl', 'directory': False},
            {'name': '.hidden.jsonl', 'path': '.hidden.jsonl', 'directory': False},
            {'name': '../outside.jsonl', 'path': '../outside.jsonl', 'directory': False},
            {'name': 'escaped%2fpath.jsonl', 'path': 'escaped%2fpath.jsonl', 'directory': False},
            {'name': '%73afe.jsonl', 'path': '%73afe.jsonl', 'directory': False},
            {'name': '%64atasets', 'path': '%64atasets', 'directory': True},
            {'name': 'unknown.jsonl', 'path': 'unknown.jsonl', 'directory': 'false'},
            {'name': 'jobs', 'path': 'jobs', 'directory': True},
        ]}))
    picker = dataset_node(page)
    expect(picker.locator('option')).to_have_count(2)
    assert not binding(page).get('dataset')
    picker.evaluate('select => { select.append(new Option("forged", "unlisted.jsonl")); select.value = "unlisted.jsonl"; select.dispatchEvent(new Event("change")); }')
    assert not binding(page).get('dataset')
    picker.select_option('safe.jsonl')
    assert binding(page)['dataset']['id'] == 'safe.jsonl'
    assert not errors


def test_dataset_binding_browsing_and_context_isolation(live_page):
    page, _app, _errors, _posts = live_page
    saved = page.locator('studio-view').evaluate('editor => ({graph: structuredClone(editor.graph), workflow: editor.workflowId})')
    for journey in (uploaded_nested_dataset_binds_only_selected_file_with_keyboard_and_narrow_theme,
                    late_binding_listing_cannot_mutate_changed_editor_or_publish_old_error,
                    picker_rejects_unlisted_paths_folders_and_malformed_rows):
        page.locator('studio-view').evaluate('''(editor, saved) => {
            clearTimeout(editor.autosave); editor.context = []; editor.workflowId = saved.workflow;
            editor.graph = structuredClone(saved.graph); editor.selected.clear(); editor.render();
        }''', saved)
        journey(live_page)

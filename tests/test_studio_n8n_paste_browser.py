"""Canvas clipboard text uses authenticated inert translation before insertion."""
import json

import pytest
from browser_expect import expect
from studio_commands import studio_command
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_n8n import node, workflow
from test_studio_n8n_browser import import_page, served  # noqa: F401

pytestmark = pytest.mark.slow


def clipboard(page, text, shortcut='ControlOrMeta+v'):
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.evaluate('text => navigator.clipboard.writeText(text)', text)
    page.locator('.studio-viewport').focus()
    page.keyboard.press(shortcut)


def insert(page):
    dialog = page.get_by_role('dialog', name='Import n8n workflow', exact=True)
    expect(dialog.get_by_role('button', name='Import draft into canvas', exact=True)).to_be_enabled()
    dialog.get_by_role('button', name='Import draft into canvas', exact=True).click()


@pytest.mark.parametrize('selection', [False, True])
def test_real_clipboard_full_and_selected_insert_remap_topology_undo(import_page, selection):  # noqa: F811
    app, _cookie, page, errors = import_page
    value = workflow([node(), node('Blocked', 'n8n-nodes-base.code', position=[500, 200],
        parameters={'jsCode': 'omit-code'}, credentials={'api': {'id': 'omit-key'}})],
        {'Start': {'main': [[{'node': 'Blocked', 'type': 'main', 'index': 0}]]}})
    if selection:
        value.pop('name')
    search = page.get_by_label('Search nodes, templates and groups', exact=True)
    search.fill('paste n8n workflow')
    expect(page.get_by_role('button', name='Import n8n / Paste JSON', exact=True)).to_be_visible()
    search.fill('')
    box = page.locator('.studio-viewport').bounding_box()
    page.mouse.move(box['x'] + 80, box['y'] + 80)
    position = page.locator('studio-view').evaluate('editor => editor.canvas.point(editor.n8nPointer)')
    viewport = page.locator('studio-view').evaluate('editor => structuredClone(editor.graph.layout.viewport)')
    clipboard(page, json.dumps(value))
    expect(page.get_by_role('dialog')).to_contain_text('Credentials')
    expect(page.locator('.studio-node')).to_have_count(0)
    insert(page)
    expect(page.locator('.studio-node')).to_have_count(2)
    first = page.locator('studio-view').evaluate('editor => structuredClone(editor.graph)')
    assert 'omit-' not in json.dumps(first)
    assert first['layout']['viewport'] == viewport
    assert first['layout']['nodes'][first['nodes'][0]['id']] == position
    page.locator('studio-view').evaluate('editor => editor.copy()')
    clipboard(page, json.dumps(value), 'Meta+v')
    insert(page)
    expect(page.locator('.studio-node')).to_have_count(4)
    expect(page.locator('.studio-connections path[data-edge-index]')).to_have_count(2)
    graph = page.locator('studio-view').evaluate('editor => editor.graph')
    ids = [entry['id'] for entry in graph['nodes']]
    assert len(set(ids)) == 4
    assert graph['nodes'][:2] == first['nodes']
    assert {edge['source']['node'] for edge in graph['edges']} <= set(ids)
    assert {edge['target']['node'] for edge in graph['edges']} <= set(ids)
    studio_command(page, 'Undo').click()
    expect(page.locator('.studio-node')).to_have_count(2)
    studio_command(page, 'Redo').click()
    expect(page.locator('.studio-node')).to_have_count(4)
    assert app.runner.snapshot() == []
    assert not errors


def test_editable_paste_dismiss_and_native_clipboard_remain_ordinary(import_page):  # noqa: F811
    _app, _cookie, page, errors = import_page
    text = json.dumps(workflow())
    assert page.locator('.studio-viewport').evaluate('view => {const event=new KeyboardEvent("keydown",{key:"v",ctrlKey:true,bubbles:true,cancelable:true});view.dispatchEvent(event);return !event.defaultPrevented}')
    page.context.grant_permissions(['clipboard-read', 'clipboard-write'])
    page.evaluate('text => navigator.clipboard.writeText(text)', text)
    title = page.get_by_label('Workflow title', exact=True)
    title.fill('')
    title.press('ControlOrMeta+v')
    expect(title).to_have_value(text)
    expect(page.get_by_role('dialog')).to_have_count(0)
    title.fill('Keep workflow')
    clipboard(page, text)
    field = page.get_by_label('n8n workflow JSON', exact=True)
    field.fill('')
    field.press('ControlOrMeta+v')
    expect(field).to_have_value(text)
    expect(page.get_by_role('dialog')).to_have_count(1)
    page.get_by_role('button', name='Cancel', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(0)
    expect(title).to_have_value('Keep workflow')
    clipboard(page, text)
    insert(page)
    page.locator('.studio-viewport').focus()
    page.keyboard.press('ControlOrMeta+c')
    page.keyboard.press('ControlOrMeta+v')
    expect(page.locator('.studio-node')).to_have_count(2)
    clipboard(page, 'ordinary text')
    expect(page.locator('.studio-node')).to_have_count(3)
    page.locator('studio-view').evaluate('editor => {const field=document.createElement("div"); field.contentEditable="true"; field.id="paste-editable"; editor.canvas.view.append(field)}')
    editable = page.locator('#paste-editable')
    editable.focus()
    page.keyboard.press('ControlOrMeta+v')
    expect(editable).to_have_text('ordinary text')
    expect(page.locator('.studio-node')).to_have_count(3)
    assert not errors


def test_malformed_oversized_and_stale_preview_never_insert(import_page):  # noqa: F811
    _app, _cookie, page, errors = import_page
    clipboard(page, '{"nodes":[],"connections": broken}')
    dialog = page.get_by_role('dialog', name='Import n8n workflow', exact=True)
    expect(dialog.get_by_role('status')).not_to_contain_text('Preparing')
    expect(dialog.get_by_role('button', name='Import draft into canvas')).to_be_disabled()
    dialog.get_by_role('button', name='Cancel', exact=True).click()
    clipboard(page, '{"nodes":[' + ' ' * 2000000)
    expect(page.locator('.studio-status')).to_contain_text('smaller than 2 MB')
    expect(page.get_by_role('dialog')).to_have_count(0)
    clipboard(page, '{"nodes":[],"connections":{},"name":"' + 'あ' * 700000 + '"}')
    expect(page.locator('.studio-status')).to_contain_text('smaller than 2 MB')
    expect(page.get_by_role('dialog')).to_have_count(0)
    held = []
    page.route('**/ui/studio/import/n8n/preview', lambda route: held.append(route))
    clipboard(page, json.dumps(workflow()))
    expect(page.get_by_role('status').filter(has_text='Preparing')).to_be_visible()
    page.locator('studio-view').evaluate('editor => editor.workflowContext++')
    held[0].fulfill(json={'title': 'STALE', 'graph': {}, 'nodes': [], 'warnings': []})
    expect(page.get_by_role('dialog')).not_to_contain_text('STALE')
    expect(page.locator('.studio-node')).to_have_count(0)
    assert not errors

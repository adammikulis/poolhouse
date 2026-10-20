"""Paste and file imports use real authenticated previews without execution."""
import json

import pytest
import test_studio_stream_http as stream_http
from browser_expect import expect
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_n8n import node, workflow

served = stream_http.served
pytestmark = pytest.mark.slow


@pytest.fixture
def import_page(served, studio_browser):  # noqa: F811
    app, cookie, _ = served
    with studio_browser() as page:
        page.context.add_cookies([{'name': 'poolhouse_ui', 'value': cookie.split('=', 1)[1],
                                  'url': f'http://127.0.0.1:{app.port}'}])
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(f'http://127.0.0.1:{app.port}/ui/#studio')
        page.get_by_role('button', name='Import n8n / Paste JSON', exact=True).wait_for()
        yield app, cookie, page, errors


def open_import(page, value):
    page.get_by_role('button', name='Import n8n / Paste JSON', exact=True).click()
    dialog = page.get_by_role('dialog', name='Import n8n workflow', exact=True)
    dialog.get_by_label('n8n workflow JSON', exact=True).fill(json.dumps(value))
    return dialog


def test_paste_preview_draft_undo_save_and_selected_run_blocker(import_page):
    app, cookie, page, errors = import_page
    posts = []
    page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' else None)
    value = workflow([node(), node('Remote <script>inert</script>', 'n8n-nodes-base.httpRequest',
        position=[500, 200], parameters={'url': 'https://private.invalid', 'header': 'omit-secret'},
        credentials={'account': {'name': 'omit-credential'}})],
        {'Start': {'main': [[{'node': 'Remote <script>inert</script>', 'type': 'main', 'index': 0}]]}})
    dialog = open_import(page, value)
    dialog.get_by_role('button', name='Preview translation', exact=True).click()
    expect(dialog.get_by_text('Start · translated', exact=True)).to_be_visible()
    expect(dialog.get_by_text('Remote <script>inert</script> · blocked', exact=True)).to_be_visible()
    assert app.ui.studio.store.records('workflow', app.ui.studio_authority._context()['scope']) == []
    dialog.get_by_role('button', name='Import draft into canvas', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(2)
    expect(page.locator('.studio-node').filter(has_text='Remote <script>inert</script>').locator('.studio-state')).to_have_text('⊘ blocked')
    expect(page.locator('.studio-connections path[data-edge-index]')).to_have_count(1)
    page.wait_for_timeout(900)
    assert not any('/workflows' in url or '/runs' in url for url in posts)
    assert 'omit-secret' not in page.locator('studio-view').evaluate('editor => JSON.stringify(editor.graph)')
    page.get_by_role('button', name='Undo', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(0)
    page.get_by_role('button', name='Redo', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(2)
    page.get_by_role('button', name='Save', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Saved · revision')
    workflow_id = page.locator('studio-view').evaluate('editor => editor.workflowId')
    _, saved, _ = app.call('/ui/studio/workflows/' + workflow_id, cookie=cookie)
    assert 'omit-' not in json.dumps(saved) and 'private.invalid' not in json.dumps(saved)
    page.locator('.studio-node').filter(has=page.locator('strong', has_text='Start')).focus()
    page.keyboard.press('Enter')
    page.get_by_role('tab', name='Runs and Results', exact=True).click()
    page.get_by_role('button', name='Run selected outputs', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('Unknown node version: import.unsupported')
    _, runs, _ = app.call('/ui/studio/runs', cookie=cookie)
    assert runs['runs'] == []
    assert app.runner.snapshot() == []
    assert not errors


def test_file_preview_requires_explicit_import_and_ignores_stale_canvas(import_page):
    _app, _cookie, page, errors = import_page
    dialog = open_import(page, workflow())
    payload = json.dumps(workflow([node('File start')])).encode()
    dialog.get_by_label('Choose n8n JSON file', exact=True).set_input_files(
        {'name': 'workflow.json', 'mimeType': 'application/json', 'buffer': payload})
    expect(dialog.get_by_role('status')).to_contain_text('File loaded')
    dialog.get_by_role('button', name='Preview translation', exact=True).click()
    expect(dialog.get_by_text('File start · translated', exact=True)).to_be_visible()
    page.locator('studio-view').evaluate('editor => editor.editSerial++')
    dialog.get_by_role('button', name='Import draft into canvas', exact=True).click()
    expect(dialog.get_by_role('status')).to_contain_text('canvas changed')
    expect(page.locator('.studio-node')).to_have_count(0)
    assert not errors


@pytest.mark.parametrize('status', [200, 403])
def test_delayed_preview_success_and_error_cannot_mutate_another_workflow(import_page, status):
    _app, _cookie, page, errors = import_page
    held = []
    page.route('**/ui/studio/import/n8n/preview', lambda route: held.append(route))
    for context in ['root', 'workflow', 'edit', 'binding', 'group', 'load', 'live', 'intent', 'hash', 'disconnect']:
        page.reload()
        page.evaluate("history.replaceState(null, '', '#studio')")
        page.evaluate("fleetModel.go('studio')")
        page.get_by_role('button', name='Import n8n / Paste JSON', exact=True).wait_for()
        held.clear()
        dialog = open_import(page, workflow())
        dialog.get_by_role('button', name='Preview translation', exact=True).click()
        expect(dialog.get_by_role('status')).to_contain_text('Preparing')
        page.locator('studio-view').evaluate("""(editor, context) => {
          if (context === 'root') editor.graph = studioModel.empty();
          if (context === 'workflow') editor.workflowId = 'other';
          if (context === 'edit') editor.editSerial++;
          if (context === 'binding') editor.graph.description = 'changed in place';
          if (context === 'group') editor.context.push({parent:editor.graph,node:{id:'nested'}});
          if (context === 'load') editor.loadContext++;
          if (context === 'live') editor.liveGeneration = (editor.liveGeneration || 0) + 1;
          if (context === 'intent') editor.selectionIntent++;
          if (context === 'hash') history.replaceState(null, '', '#other');
          if (context === 'disconnect') { window.removedImportEditor = editor; editor.parentNode.removeChild(editor); }
        }""", context)
        with page.expect_response('**/ui/studio/import/n8n/preview') as received:
            held[0].fulfill(status=status, json={'error': 'STALE ERROR'} if status == 403 else {
                'title': 'STALE PREVIEW', 'graph': {}, 'nodes': [], 'warnings': []})
        received.value.body()
        page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
        if context == 'disconnect':
            assert page.evaluate('!window.removedImportEditor.isConnected')
            assert page.evaluate('window.removedImportEditor.graph.nodes.length') == 0
            assert not page.evaluate('window.removedImportEditor.n8nDialog.textContent.includes("STALE")')
            expect(page.locator('.studio-node')).to_have_count(0)
            assert not errors
            continue
        expect(dialog.get_by_role('button', name='Import draft into canvas', exact=True)).to_be_disabled()
        expect(dialog).not_to_contain_text('STALE')
        expect(page.locator('.studio-node')).to_have_count(0)
        assert not errors


def test_preview_endpoint_requires_authenticated_current_session(served):
    app, cookie, session = served
    endpoint = '/ui/studio/import/n8n/preview'
    value = {'json': json.dumps(workflow())}
    assert app.call(endpoint, method='POST', body=value, cookie='')[0] in {401, 403}
    status, response, _ = app.call(endpoint, method='POST', body=value, cookie=cookie)
    assert status == 200, response
    app.ui.sessions.close(session.sid)
    assert app.call(endpoint, method='POST', body=value, cookie=cookie)[0] in {401, 403}


def test_import_fit_is_the_baseline_for_the_next_title_edit(import_page):
    _app, _cookie, page, errors = import_page
    dialog = open_import(page, workflow([node(), node('Other', 'n8n-nodes-base.code', position=[1800, 1100])]))
    dialog.get_by_role('button', name='Preview translation', exact=True).click()
    expect(dialog.get_by_role('status')).not_to_contain_text('Preparing')
    expect(dialog.get_by_role('button', name='Import draft into canvas', exact=True)).to_be_enabled()
    dialog.get_by_role('button', name='Import draft into canvas', exact=True).click()
    fitted = page.locator('studio-view').evaluate('editor => structuredClone(editor.graph)')
    assert fitted['layout']['viewport'] != {'x': 0, 'y': 0, 'zoom': 1}
    title = page.get_by_label('Workflow title', exact=True)
    title.fill('Edited title')
    title.press('Tab')
    page.get_by_role('button', name='Undo', exact=True).click()
    assert page.locator('studio-view').evaluate('editor => editor.graph') == fitted
    expect(title).to_have_value('Imported example')
    page.get_by_role('button', name='Undo', exact=True).click()
    expect(page.locator('.studio-node')).to_have_count(0)
    page.get_by_role('button', name='Redo', exact=True).click()
    assert page.locator('studio-view').evaluate('editor => editor.graph') == fitted
    assert not errors

"""Initial autosave acknowledgements retain the same inert import preview."""
import json

import pytest
from browser_expect import expect
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_n8n import workflow
from test_studio_n8n_browser import import_page, served  # noqa: F401

pytestmark = pytest.mark.slow


def pending_preview(page):
    saves, previews = [], []
    page.route('**/ui/studio/workflows', lambda route: saves.append(route) if route.request.method == 'POST' else route.continue_())
    page.route('**/ui/studio/import/n8n/preview', lambda route: previews.append(route))
    title = page.get_by_label('Workflow title', exact=True)
    title.fill('Retained draft')
    title.press('Tab')
    page.get_by_role('button', name='Import n8n / Paste JSON', exact=True).click()
    dialog = page.get_by_role('dialog', name='Import n8n workflow', exact=True)
    dialog.get_by_label('n8n workflow JSON', exact=True).fill(json.dumps(workflow()))
    dialog.get_by_role('button', name='Preview translation', exact=True).click()
    expect(dialog.get_by_role('status')).to_contain_text('Preparing')
    page.wait_for_function('() => !!document.querySelector("studio-view").saving')
    assert page.locator('studio-view').evaluate('editor => editor.workflowId || null') is None
    return dialog, saves, previews


def release(route):
    response = route.fetch()
    assert response.ok
    route.fulfill(response=response)


@pytest.mark.parametrize('save_first', [False, True])
def test_actual_initial_autosave_can_finish_before_or_after_preview(import_page, save_first):  # noqa: F811
    app, _cookie, page, errors = import_page
    dialog, saves, previews = pending_preview(page)
    page.wait_for_function('() => !!document.querySelector("studio-view").saving')
    if save_first:
        release(saves[0])
        expect(page.locator('.studio-status')).to_contain_text('Saved · revision 1')
        release(previews[0])
    else:
        release(previews[0])
        expect(dialog.get_by_role('button', name='Import draft into canvas')).to_be_enabled()
        release(saves[0])
        expect(page.locator('.studio-status')).to_contain_text('Saved · revision 1')
    identity = page.locator('studio-view').evaluate('editor => editor.workflowId')
    expect(dialog.get_by_role('button', name='Import draft into canvas')).to_be_enabled()
    dialog.get_by_role('button', name='Import draft into canvas').click()
    expect(page.locator('.studio-node')).to_have_count(1)
    assert page.locator('studio-view').evaluate('editor => editor.workflowId') == identity
    assert app.runner.snapshot() == []
    assert not errors


@pytest.mark.parametrize('change', ['new', 'identical-open', 'edit', 'session', 'unacknowledged-id'])
def test_initial_save_exception_never_adopts_another_preview_context(import_page, change):  # noqa: F811
    _app, _cookie, page, errors = import_page
    dialog, saves, previews = pending_preview(page)
    if change == 'session':
        page.evaluate('() => fleetModel.signOut()')
        for route in (previews[0], saves[0]):
            response = route.fetch()
            assert response.status in {401, 403}
            route.fulfill(response=response)
        expect(page.locator('.studio-node')).to_have_count(0)
        assert not errors
        return
    page.locator('studio-view').evaluate('''(editor, change) => {
      if (change === 'new') void editor.open(null);
      if (change === 'identical-open') void editor.open({id:'other-workflow',revision:1,title:'Other',graph:structuredClone(editor.graph)});
      if (change === 'edit') {editor.graph.description='changed';editor.changed(false)}
      if (change === 'unacknowledged-id') editor.workflowId='other-workflow';
    }''', change)
    release(previews[0])
    if change != 'unacknowledged-id':
        release(saves[0])
    expect(dialog.get_by_role('button', name='Import draft into canvas')).to_be_disabled()
    expect(page.locator('.studio-node')).to_have_count(0)
    if change == 'unacknowledged-id':
        release(saves[0])
    assert not errors

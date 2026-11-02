"""Inserted nodes remain visible after the selected inspector changes layout."""
import json

import pytest
from browser_expect import expect
from studio_commands import studio_command
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_n8n import workflow
from test_studio_n8n_browser import import_page, served  # noqa: F401
from test_studio_n8n_paste_browser import clipboard, insert

pytestmark = pytest.mark.slow


def bounds(page):
    return page.locator('studio-view').evaluate('''editor => {
      const card=editor.canvas.layer.querySelector('.studio-node').getBoundingClientRect();
      const view=editor.canvas.view.getBoundingClientRect();
      const map=editor.canvas.querySelector('.studio-minimap').getBoundingClientRect();
      return {card:card.toJSON(),view:view.toJSON(),map:map.toJSON()};
    }''')


@pytest.mark.parametrize('width', [960, 1440])
def test_post_selection_layout_reveals_only_clipped_imports_and_keeps_one_undo(import_page, width):  # noqa: F811
    _app, _cookie, page, errors = import_page
    page.set_viewport_size({'width':width,'height':1000})
    view = page.locator('.studio-viewport').bounding_box()
    page.mouse.move(view['x'] + (view['width']/2 if width == 960 else 5),
                    view['y'] + (view['height']/2 if width == 960 else 5))
    anchor = page.locator('studio-view').evaluate('editor => editor.canvas.point(editor.n8nPointer)')
    before = page.locator('studio-view').evaluate('editor => structuredClone(editor.graph.layout.viewport)')
    clipboard(page, json.dumps(workflow()))
    insert(page)
    page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
    rectangles = bounds(page)
    card, view, minimap = rectangles['card'], rectangles['view'], rectangles['map']
    assert card['left'] >= view['left'] and card['top'] >= view['top']
    assert card['right'] <= view['right'] and card['bottom'] <= view['bottom']
    assert not (card['left'] < minimap['right'] and card['right'] > minimap['left'] and
                card['top'] < minimap['bottom'] and card['bottom'] > minimap['top'])
    after = page.locator('studio-view').evaluate('editor => structuredClone(editor.graph)')
    assert after['layout']['nodes'][after['nodes'][0]['id']] == anchor
    if width == 1440:
        assert after['layout']['viewport'] == before
    else:
        assert after['layout']['viewport'] != before
    studio_command(page, 'Undo').click()
    expect(page.locator('.studio-node')).to_have_count(0)
    studio_command(page, 'Redo').click()
    assert page.locator('studio-view').evaluate('editor => editor.graph') == after
    assert not errors


def test_delayed_reveal_cannot_amend_a_newer_viewport_history_entry(import_page):  # noqa: F811
    _app, _cookie, page, errors = import_page
    page.set_viewport_size({'width':960,'height':1000})
    clipboard(page, json.dumps(workflow()))
    expect(page.get_by_role('button', name='Import draft into canvas')).to_be_enabled()
    page.evaluate('''() => {
      window.nativeFrame=requestAnimationFrame;window.heldFrames=[];
      window.requestAnimationFrame=callback => {window.heldFrames.push(callback);return 0};
    }''')
    try:
        page.get_by_role('button', name='Import draft into canvas').click()
        page.get_by_role('button', name='Zoom in', exact=True).click()
        after = page.locator('studio-view').evaluate('editor => ({graph:structuredClone(editor.graph),history:editor.undoStack.length})')
    finally:
        page.evaluate('''() => {window.requestAnimationFrame=window.nativeFrame;
          for(const callback of window.heldFrames.splice(0)) callback(performance.now());}''')
    assert page.locator('studio-view').evaluate('editor => ({graph:editor.graph,history:editor.undoStack.length})') == after
    assert not errors

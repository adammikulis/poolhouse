"""Nested Studio canvases keep an obvious route to their containing workflow."""
import pytest
from browser_expect import expect
from studio_commands import studio_command
from test_studio_canvas_browser import add, studio_browser, studio_page  # noqa: F401

from poolhouse.fleet.themes import PRESETS

pytestmark = pytest.mark.slow


def named_group(page, name, proof=None):
    studio_command(page, 'Group selection').click()
    if proof:
        page.screenshot(path=proof / 'group-ready.png')
    opener = page.locator('.studio-node').get_by_role('button', name='Open group', exact=True)
    (opener if opener.is_visible() else page.locator('.studio-inspector').get_by_role('button', name='Open group', exact=True)).click()
    field = page.get_by_label('Group name', exact=True)
    field.fill(name)
    field.press('Tab')


def test_nested_group_breadcrumb_returns_to_root_without_losing_edits(studio_page, tmp_path):  # noqa: F811
    page, state, errors = studio_page
    title = page.get_by_label('Workflow title', exact=True)
    title.fill('Fraud review')
    title.press('Tab')
    add(page, 'Prompt template')
    page.get_by_role('button', name='Zoom in', exact=True).click()
    root_view = page.locator('studio-view').evaluate('e=>structuredClone(e.graph.layout.viewport)')
    page.screenshot(path=tmp_path / 'root-entry.png')
    named_group(page, 'Prepare evidence', tmp_path)
    page.screenshot(path=tmp_path / 'group-open.png')
    page.locator('.studio-node header').click()
    named_group(page, 'Check prompt')
    page.screenshot(path=tmp_path / 'nested-open.png')
    nav = page.get_by_role('navigation', name='Canvas location', exact=True)
    expect(nav.get_by_role('button', name='Back to Prepare evidence', exact=True)).to_be_visible()
    expect(nav.locator('[aria-current="location"]')).to_have_text('Check prompt')
    page.locator('.studio-node header').click()
    prompt = page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)
    prompt.fill('Retain this nested edit')
    prompt.press('Tab')
    page.screenshot(path=tmp_path / 'nested-edited.png')
    before = page.locator('studio-view').evaluate('e=>({serial:e.editSerial,undo:e.undoStack.length})')
    root = nav.get_by_role('button', name='Workflow: Fraud review', exact=True)
    root.focus()
    root.press('Enter')
    page.screenshot(path=tmp_path / 'root-return.png')
    expect(nav.locator('[aria-current="location"]')).to_have_text('Fraud review')
    assert page.locator('studio-view').evaluate('e=>e.context.length') == 0
    assert page.locator('studio-view').evaluate('e=>e.graph.layout.viewport') == root_view
    assert page.locator('studio-view').evaluate('e=>({serial:e.editSerial,undo:e.undoStack.length})') == before
    expect(page.locator('.studio-node[aria-selected="true"]')).to_have_count(1)
    page.locator('.studio-node').get_by_role('button', name='Open group', exact=True).click()
    page.locator('.studio-node').get_by_role('button', name='Open group', exact=True).click()
    page.locator('.studio-node header').click()
    expect(page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)).to_have_value('Retain this nested edit')
    page.locator('.studio-viewport').focus()
    page.keyboard.press('Escape')
    expect(nav.locator('[aria-current="location"]')).to_have_text('Prepare evidence')
    expect(nav.get_by_role('button', name='Back to Fraud review', exact=True)).to_be_visible()
    page.screenshot(path=tmp_path / 'back-to-parent.png')
    assert not state['submissions'] and not errors


def test_detached_group_controls_cannot_mutate_a_different_canvas(studio_page):  # noqa: F811
    page, _state, errors = studio_page
    add(page, 'Prompt template')
    named_group(page, 'Outer review')
    page.locator('.studio-node header').click()
    named_group(page, 'Inner review')
    page.evaluate('''() => {
      window.oldGroupName=document.querySelector('[aria-label="Group name"]');
      window.oldAncestor=document.querySelector('[aria-label="Group: Outer review"]');
    }''')
    page.get_by_role('button', name='Workflow: Untitled experiment', exact=True).click()
    before = page.locator('studio-view').evaluate('e=>({graph:structuredClone(e.rootGraph()),serial:e.editSerial})')
    page.evaluate('''() => {
      oldGroupName.value="Detached overwrite";
      oldGroupName.dispatchEvent(new Event("change"));
      oldAncestor.dispatchEvent(new MouseEvent("click"));
      const editor=document.querySelector("studio-view");
      for(const depth of [-1,0,1,NaN,Infinity,"0"]) editor.leaveGroup(depth);
    }''')
    assert page.locator('studio-view').evaluate('e=>({graph:structuredClone(e.rootGraph()),serial:e.editSerial})') == before
    assert page.locator('studio-view').evaluate('e=>e.context.length') == 0
    assert not errors


def test_library_filters_preserve_graph_focus_and_selected_inspector(studio_page, tmp_path):  # noqa: F811
    page, _state, errors = studio_page
    expect(page.locator('.studio-inspector')).to_be_hidden()
    page.screenshot(path=tmp_path / 'empty-root.png')
    add(page, 'Prompt template')
    expect(page.locator('.studio-inspector')).to_be_visible()
    editor = page.locator('studio-view')
    before = editor.evaluate('e=>({graph:structuredClone(e.rootGraph()),selected:[...e.selected],serial:e.editSerial,undo:e.undoStack.length})')
    category = page.get_by_label('Library category', exact=True)
    category.focus()
    category.select_option(label='Results')
    page.screenshot(path=tmp_path / 'category-filtered.png')
    expect(page.locator('.studio-library-node[data-node-type="prompt"]')).to_have_count(0)
    expect(page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)).to_be_visible()
    editor.evaluate('e=>e.render()')
    expect(category).to_be_focused()
    assert editor.evaluate('e=>({graph:structuredClone(e.rootGraph()),selected:[...e.selected],serial:e.editSerial,undo:e.undoStack.length})') == before
    category.select_option(label='All')
    button = page.get_by_role('button', name='Add Report', exact=True)
    button.focus()
    editor.evaluate('e=>e.render()')
    expect(button).to_be_focused()
    button.press('Enter')
    page.screenshot(path=tmp_path / 'keyboard-added.png')
    expect(page.locator('.studio-inspector').get_by_role('heading', name='Report', exact=True)).to_be_visible()
    viewport = editor.evaluate('e=>structuredClone(e.graph.layout.viewport)')
    page.locator('.studio-viewport').focus()
    page.keyboard.press('Escape')
    expect(page.locator('.studio-inspector')).to_be_hidden()
    assert editor.evaluate('e=>e.graph.layout.viewport') == viewport
    assert not errors


@pytest.mark.parametrize('width,height', [(1440, 900), (1440, 600), (390, 844)])
@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_group_navigation_is_compact_and_interface_controls_stay_in_inspector(studio_page, width, height, theme, tmp_path):  # noqa: F811
    page, _state, errors = studio_page
    page.set_viewport_size({'width': width, 'height': height})
    page.evaluate('theme=>fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
    add(page, 'Prompt template')
    named_group(page, 'Reusable validation')
    nav = page.get_by_role('navigation', name='Canvas location', exact=True)
    expect(nav.get_by_role('button', name='Back to Untitled experiment', exact=True)).to_be_in_viewport()
    expect(nav.locator('[aria-current="location"]')).to_be_in_viewport()
    assert nav.locator('[aria-current="location"]').bounding_box()['width'] >= 64
    assert nav.get_by_role('textbox').count() == 0
    expect(page.locator('.studio-inspector').get_by_label('Group name', exact=True)).to_be_visible()
    for summary in page.locator('.studio-command > summary').all():
        rect = summary.bounding_box()
        assert rect['x'] >= 0 and rect['x'] + rect['width'] <= width
    page.screenshot(path=tmp_path / 'studio-navigation.png')
    assert nav.bounding_box()['height'] <= 42
    assert page.locator('#studio-panel-canvas').bounding_box()['height'] >= height * .45
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth && document.documentElement.scrollHeight<=innerHeight')
    expect(page.get_by_role('button', name='Run workflow', exact=True)).to_have_attribute('title', 'Run the entire workflow, including nodes outside this group')
    assert not errors

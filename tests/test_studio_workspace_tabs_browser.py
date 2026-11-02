"""Studio editing and run history share a bounded, keyboard-accessible workspace."""
import pytest
from browser_expect import expect
from test_studio_canvas_browser import add, studio_browser, studio_page  # noqa: F401

from poolhouse.fleet.themes import PRESETS

pytestmark = pytest.mark.slow


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
def test_canvas_and_results_tabs_keep_drafts_and_scroll_inside_workspace(studio_page, theme, width):  # noqa: F811
    page, state, errors = studio_page
    page.set_viewport_size({'width': width, 'height': 850})
    page.wait_for_function('() => !!fleetModel.preferences.resolved_theme')
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
    add(page, 'Prompt template')
    prompt = page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)
    prompt.fill('Keep this draft')
    editor = page.locator('studio-view')
    before = editor.evaluate('e => ({selected:[...e.selected],context:e.context.map(c=>c.node.id),view:structuredClone(e.graph.layout.view)})')
    state['runs'] = [{'id': f'run-{i}', 'state': 'done', 'revision': i, 'nodes': {}} for i in range(100)]
    editor.evaluate('e => e.refreshRuns()')
    canvas = page.get_by_role('tab', name='Canvas', exact=True)
    results = page.get_by_role('tab', name='Runs and Results', exact=True)
    expect(canvas).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#studio-panel-results')).to_be_hidden()
    canvas.focus()
    canvas.press('ArrowRight')
    expect(results).to_be_focused()
    expect(results).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#studio-panel-canvas')).to_be_hidden()
    page.locator('#studio-panel-results').evaluate('e => e.scrollTop=e.scrollHeight')
    expect(page.get_by_role('button', name='run-99', exact=True)).to_be_in_viewport()
    assert page.locator('#studio-panel-results').evaluate('e => e.scrollHeight > e.clientHeight && e.scrollTop > 0')
    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight'), page.evaluate('() => ({height:innerHeight,scroll:document.documentElement.scrollHeight,studio:document.querySelector(".studio-workspace").getBoundingClientRect().toJSON(),workspace:document.querySelector("#workspace").getBoundingClientRect().toJSON(),rail:document.querySelector(".workspace-rail").getBoundingClientRect().toJSON()})')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    results.press('Home')
    expect(canvas).to_be_focused()
    expect(prompt).to_have_value('Keep this draft')
    assert editor.evaluate('e => ({selected:[...e.selected],context:e.context.map(c=>c.node.id),view:structuredClone(e.graph.layout.view)})') == before
    expect(page.get_by_role('button', name='Run workflow', exact=True)).to_be_visible()
    if width == 1440:
        assert page.locator('studio-canvas').evaluate('e => e.clientHeight > 300')
    else:
        expect(page.locator('.studio-outline')).to_be_visible()
        expect(prompt).to_be_in_viewport()
    assert not errors


def test_invalid_canvas_draft_is_revealed_from_results_without_submitting(studio_page):  # noqa: F811
    page, state, errors = studio_page
    add(page, 'Report')
    page.get_by_text('Sweep parameters', exact=True).click()
    draft = page.get_by_label('Sweep iterations', exact=True)
    draft.fill('not an integer')
    page.get_by_role('tab', name='Runs and Results', exact=True).click()
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.get_by_role('tab', name='Canvas', exact=True)).to_have_attribute('aria-selected', 'true')
    expect(page.get_by_label('Sweep iterations', exact=True)).to_be_focused()
    expect(page.get_by_label('Sweep iterations', exact=True)).to_have_value('not an integer')
    assert state['submissions'] == []
    assert not errors

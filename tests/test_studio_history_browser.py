"""Bounded editing history remains independent from submitted execution and settings."""
import pytest
import test_studio_canvas_browser
from browser_expect import expect
from studio_commands import studio_command
from test_studio_canvas_browser import add, studio_browser  # noqa: F401

studio_page = test_studio_canvas_browser.studio_page
pytestmark = pytest.mark.slow


def history(page):
    return page.locator('studio-view').evaluate('e => [e.undoStack.length,e.redoStack.length]')


def undo(page, redo=False):
    studio_command(page, 'Redo' if redo else 'Undo').click()


def test_settings_limit_persists_and_trims_both_stacks_to_nearest_states(studio_page):
    page, _state, errors = studio_page
    add(page, 'Prompt template')
    page.locator('studio-view').evaluate('''e => {
      for(let i=0;i<140;i++) { e.checkpoint(); e.graph.nodes[0].parameters.template=String(i); e.changed(false); }
    }''')
    assert history(page) == [32, 0]
    page.evaluate('location.hash="settings"')
    limit = page.get_by_label('Studio undo steps', exact=True)
    expect(limit).to_have_value('32')
    limit.fill('128')
    page.get_by_role('button', name='Save preferences', exact=True).click()
    expect(page.locator('#settings-note')).to_contain_text('Preferences saved')
    page.evaluate('location.hash="studio"')
    page.locator('studio-view').evaluate('''e => {
      for(let i=140;i<280;i++) { e.checkpoint(); e.graph.nodes[0].parameters.template=String(i); e.changed(false); }
      for(let i=0;i<10;i++) e.undo();
    }''')
    assert history(page) == [118, 10]
    page.evaluate('location.hash="settings"')
    expect(limit).to_have_value('128')
    limit.fill('3')
    page.get_by_role('button', name='Save preferences', exact=True).click()
    expect(page.locator('#settings-note')).to_contain_text('Preferences saved')
    assert history(page) == [3, 3]
    page.evaluate('location.hash="studio"')
    undo(page, True)
    assert page.locator('studio-view').evaluate('e=>e.graph.nodes[0].parameters.template') == '270'
    page.locator('studio-view').evaluate('e=>{e.checkpoint();e.changed(false)}')
    assert history(page) == [3, 2]  # no-op does not destroy redo
    field = page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)
    # Undo deselects; select the node through the actual canvas before editing.
    page.locator('.studio-node header').click()
    field.fill('a new branch')
    field.press('Tab')
    assert history(page) == [3, 0]
    page.reload()
    page.evaluate('location.hash="settings"')
    expect(page.get_by_label('Studio undo steps', exact=True)).to_have_value('3')
    assert not errors


def test_title_viewport_group_navigation_and_arrange_are_atomic_draft_edits(studio_page):
    page, state, errors = studio_page
    add(page, 'Prompt template')
    title = page.get_by_label('Workflow title', exact=True)
    title.fill('An editable experiment')
    title.press('Tab')
    undo(page)
    expect(title).to_have_value('Untitled experiment')
    undo(page, True)
    expect(title).to_have_value('An editable experiment')
    before = page.locator('studio-view').evaluate('e=>structuredClone(e.graph.layout)')
    page.locator('studio-view').evaluate('e=>e.canvas.zoom(1.2)')
    undo(page)
    assert page.locator('studio-view').evaluate('e=>e.graph.layout') == before
    undo(page, True)
    page.locator('.studio-node header').click()
    studio_command(page, 'Group selection').click()
    page.get_by_role('button', name='Open group', exact=True).first.click()
    name = page.get_by_label('Group name', exact=True)
    name.fill('Named reusable group')
    name.press('Tab')
    page.locator('.studio-viewport').focus()
    page.keyboard.press('Escape')
    undo(page)
    expect(page.get_by_label('Group name', exact=True)).to_have_value('Reusable group')
    undo(page)  # undo grouping across navigation, safely restores root context
    assert page.locator('studio-view').evaluate('e=>e.context.length') == 0
    expect(page.locator('.studio-node')).to_have_count(1)
    undo(page, True)
    assert page.locator('studio-view').evaluate('e=>e.rootGraph().nodes[0].type') == 'group'
    depth = history(page)[0]
    studio_command(page, 'Arrange').click()
    assert history(page)[0] == depth + 1
    arranged = page.locator('studio-view').evaluate('e=>structuredClone(e.graph.layout)')
    undo(page)
    undo(page, True)
    assert page.locator('studio-view').evaluate('e=>e.graph.layout') == arranged
    assert not state['submissions'] and not errors


def test_node_configuration_connections_copy_delete_and_run_history_are_independent(studio_page):
    page, state, errors = studio_page
    add(page, 'Prompt template')
    first = page.locator('studio-view').evaluate('e=>structuredClone(e.rootGraph())')
    add(page, 'Report')
    undo(page)
    assert page.locator('studio-view').evaluate('e=>e.rootGraph()') == first
    undo(page, True)
    page.locator('.studio-node header').last.click()
    picker = page.locator('.studio-inspector').get_by_label('Connect text', exact=True)
    picker.select_option(index=1)
    expect(page.locator('.studio-connections path')).to_have_count(1)
    undo(page)
    expect(page.locator('.studio-connections path')).to_have_count(0)
    undo(page, True)
    expect(page.locator('.studio-connections path')).to_have_count(1)
    page.locator('.studio-node header').first.click()
    field = page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)
    field.fill('An edited prompt')
    field.press('Tab')
    undo(page)
    assert page.locator('studio-view').evaluate('e=>e.graph.nodes[0].parameters.template') == 'Hello'
    undo(page, True)
    page.locator('.studio-node header').first.click()
    studio_command(page, 'Copy').click()
    studio_command(page, 'Paste').click()
    expect(page.locator('.studio-node')).to_have_count(3)
    undo(page)
    expect(page.locator('.studio-node')).to_have_count(2)
    undo(page, True)
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('#studio-runs')).to_contain_text('done')
    undo(page)
    expect(page.locator('.studio-node')).to_have_count(2)
    assert len(state['submissions']) == 1 and len(state['runs']) == 1
    assert not errors


def test_arrange_only_viewport_change_is_one_step_and_noop_preserves_redo(studio_page):
    page, _state, errors = studio_page
    add(page, 'Prompt template')
    studio_command(page, 'Arrange').click()
    fitted = page.locator('studio-view').evaluate('e=>structuredClone(e.graph.layout.viewport)')
    page.locator('studio-view').evaluate('e=>e.canvas.zoom(1.2)')
    before = page.locator('studio-view').evaluate('e=>structuredClone(e.graph.layout.viewport)')
    depth = history(page)[0]
    studio_command(page, 'Arrange').click()
    assert history(page)[0] == depth + 1
    undo(page)
    assert page.locator('studio-view').evaluate('e=>e.graph.layout.viewport') == before
    undo(page, True)
    assert page.locator('studio-view').evaluate('e=>e.graph.layout.viewport') == fitted
    # Create redo without changing the already arranged layout.
    page.get_by_label('Workflow title', exact=True).fill('Temporary title')
    page.get_by_label('Workflow title', exact=True).press('Tab')
    undo(page)
    counts = history(page)
    studio_command(page, 'Arrange').click()
    assert history(page) == counts
    assert not errors

"""Canvas context arrangement delegates the existing undoable layout command."""
import pytest
import test_studio_placement_browser
from browser_expect import expect
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_placement_browser import connected, drop_at, graph, wire_point

studio_page = test_studio_placement_browser.studio_page
placement_page = test_studio_placement_browser.placement_page
pytestmark = pytest.mark.slow


@pytest.mark.parametrize('origin', ['blank', 'wire', 'keyboard'])
def test_arrange_nodes_stays_outside_search_and_is_one_keyboard_undo(placement_page, origin):
    page, _state, errors = placement_page
    connected(page)
    before = graph(page)
    history = page.locator('studio-view').evaluate('editor => editor.undoStack.length')
    viewport = page.locator('.studio-viewport')
    if origin == 'wire':
        point = wire_point(page)
        page.mouse.click(point['x'], point['y'], button='right')
    elif origin == 'keyboard':
        viewport.focus()
        viewport.press('Shift+F10')
    else:
        viewport.click(button='right', position={'x': 30, 'y': 430})
    menu = page.get_by_role('dialog')
    search = page.get_by_label('Search nodes to place', exact=True)
    search.fill('No catalogue item has this name')
    expect(menu.locator('.studio-placement-choices button')).to_have_count(0)
    action = menu.get_by_role('button', name='Arrange nodes', exact=True)
    expect(action).to_be_visible()
    assert action.evaluate('(e) => getComputedStyle(e).backgroundColor') == 'rgba(0, 0, 0, 0)'
    assert menu.evaluate('(e) => getComputedStyle(e).backgroundColor') == page.get_by_role('button', name='Arrange', exact=True).evaluate('(e) => getComputedStyle(e).backgroundColor')
    search.press('Shift+Tab')
    expect(action).to_be_focused()
    action.press('Enter')
    expect(menu).to_have_count(0)
    expect(viewport).to_be_focused()
    after = graph(page)
    assert after['layout'] != before['layout']
    assert after['edges'] == before['edges']
    assert after['nodes'] == before['nodes']
    assert page.locator('studio-view').evaluate('editor => editor.undoStack.length') == history + 1
    expect(page.locator('.studio-node[aria-selected="true"]')).to_have_count(1)
    viewport.press('Control+z')
    assert graph(page) == before
    expect(viewport).to_be_focused()
    expect(page.locator('.studio-node[aria-selected="true"]')).to_have_count(0)
    viewport.press('Control+Shift+z')
    assert graph(page) == after
    assert not errors


def test_arrange_is_not_offered_in_port_chooser_or_native_text_context(placement_page):
    page, _state, errors = placement_page
    connected(page)
    drop_at(page, 'ambiguous', wire_point(page))
    chooser = page.get_by_role('dialog', name='Choose connection ports')
    expect(chooser).to_be_visible()
    expect(chooser.get_by_role('button', name='Arrange nodes', exact=True)).to_have_count(0)
    page.get_by_label('Search nodes to place', exact=True).press('Escape')
    text = page.locator('.studio-inspector').get_by_label('title', exact=True)
    text.focus()
    text.press('Shift+F10')
    expect(page.get_by_role('dialog')).to_have_count(0)
    page.locator('.studio-node header').first.click(button='right')
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert not errors

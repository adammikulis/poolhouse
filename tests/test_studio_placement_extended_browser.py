"""Port suggestions, transformed context menus and stale nested placement."""
import pytest
import test_studio_placement_browser
from browser_expect import expect
from test_studio_canvas_browser import add, studio_browser  # noqa: F401
from test_studio_placement_browser import graph

placement_page = test_studio_placement_browser.placement_page
studio_page = test_studio_placement_browser.studio_page
pytestmark = pytest.mark.slow


def test_dragged_port_suggestion_uses_release_position_and_atomic_undo(placement_page):
    page, _state, errors = placement_page
    add(page, 'Prompt template')
    before = graph(page)
    port = page.locator('.studio-node [data-side="outputs"][data-port="text"]')
    box = page.locator('.studio-viewport').bounding_box()
    end = {'x': box['x'] + 100, 'y': box['y'] + 400}
    origin = port.bounding_box()
    page.mouse.move(origin['x'] + 10, origin['y'] + 10)
    page.mouse.down()
    page.mouse.move(end['x'], end['y'], steps=8)
    page.mouse.up()
    button = page.locator('.studio-library-node[data-node-type="report"] button')
    button.focus()
    button.press('Enter')
    expect(page.locator('.studio-status')).to_contain_text('added and connected')
    current = graph(page)
    report = next(n for n in current['nodes'] if n['type'] == 'report')
    assert current['layout']['nodes'][report['id']] == pytest.approx({'x': 100, 'y': 400}, abs=2)
    assert len(current['edges']) == 1
    page.get_by_role('button', name='Undo', exact=True).click()
    assert graph(page) == before
    assert not errors


def test_context_placement_uses_transformed_world_coordinates(placement_page):
    page, _state, errors = placement_page
    page.locator('studio-view').evaluate('''editor => {
      Object.assign(editor.graph.layout.viewport,{x:35,y:20,zoom:.75});editor.canvas.transform();}''')
    page.locator('.studio-viewport').click(button='right', position={'x': 240, 'y': 180})
    page.get_by_label('Search nodes to place', exact=True).fill('Text relay')
    page.get_by_role('dialog').get_by_role('button', name='Text relay Tests', exact=True).click()
    current = graph(page)
    assert current['layout']['nodes'][current['nodes'][0]['id']] == pytest.approx({'x': (240-35)/.75, 'y': (180-20)/.75}, abs=2)
    assert not errors


def test_context_layer_change_closes_stale_menu(placement_page):
    page, _state, errors = placement_page
    add(page, 'Prompt template')
    page.get_by_role('button', name='Group selection', exact=True).click()
    page.locator('.studio-viewport').click(button='right', position={'x': 30, 'y': 30})
    expect(page.get_by_role('dialog')).to_be_visible()
    page.get_by_role('button', name='Open group', exact=True).first.click()
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(graph(page)['nodes']) == 1
    assert not errors


def test_changed_group_output_rejects_stale_port_suggestion(placement_page):
    page, _state, errors = placement_page
    add(page, 'Prompt template')
    page.get_by_role('button', name='Group selection', exact=True).click()
    page.get_by_role('button', name='Open group', exact=True).first.click()
    page.locator('.studio-node').focus()
    page.keyboard.press('Enter')
    exposed = page.get_by_label('Expose outputs text', exact=True)
    exposed.fill('public')
    exposed.press('Tab')
    page.locator('.studio-viewport').focus()
    page.keyboard.press('Escape')
    page.locator('.studio-node [data-side="outputs"][data-port="public"]').click()
    page.get_by_role('button', name='Open group', exact=True).first.click()
    page.locator('.studio-node').focus()
    page.keyboard.press('Enter')
    page.get_by_label('Expose outputs text', exact=True).fill('changed')
    page.get_by_label('Expose outputs text', exact=True).press('Tab')
    page.locator('.studio-viewport').focus()
    page.keyboard.press('Escape')
    before = graph(page)
    button = page.locator('.studio-library-node[data-node-type="report"] button')
    button.focus()
    button.press('Enter')
    expect(page.locator('.studio-status')).to_contain_text('selected port changed')
    assert graph(page) == before
    assert page.locator('studio-view').evaluate('editor => editor.canvas.pending') is None
    assert not errors


def test_context_to_port_chooser_escape_returns_focus_to_canvas(placement_page):
    page, _state, errors = placement_page
    test_studio_placement_browser.connected(page)
    point = test_studio_placement_browser.wire_point(page)
    page.mouse.click(point['x'], point['y'], button='right')
    page.get_by_label('Search nodes to place', exact=True).fill('Multiple ports')
    page.get_by_role('dialog').get_by_role('button', name='Multiple ports Tests', exact=True).click()
    chooser = page.get_by_role('dialog', name='Choose connection ports')
    expect(chooser).to_be_visible()
    page.get_by_label('Search nodes to place', exact=True).press('Escape')
    expect(chooser).to_have_count(0)
    expect(page.locator('.studio-viewport')).to_be_focused()
    assert len(graph(page)['nodes']) == 2
    assert not errors

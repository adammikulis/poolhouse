"""Direct manipulation of typed Studio nodes through browser pointer journeys."""
import pytest
import test_studio_canvas_browser
from browser_expect import expect
from test_studio_canvas_browser import add, studio_browser  # noqa: F401

pytestmark = pytest.mark.slow
studio_page = test_studio_canvas_browser.studio_page


@pytest.fixture
def placement_page(request, monkeypatch):
    extras = [
        {'type': 'relay', 'version': 1, 'title': 'Text relay', 'family': 'Tests',
         'inputs': [{'name': 'in', 'type': 'text'}], 'outputs': [{'name': 'out', 'type': 'text'}], 'parameters': {}},
        {'type': 'ambiguous', 'version': 1, 'title': 'Multiple ports', 'family': 'Tests',
         'inputs': [{'name': 'first', 'type': 'text'}, {'name': 'second', 'type': 'text'}],
         'outputs': [{'name': 'out', 'type': 'text'}], 'parameters': {}},
    ]
    monkeypatch.setattr(test_studio_canvas_browser, 'SPECS', test_studio_canvas_browser.SPECS + extras)
    return request.getfixturevalue('studio_page')


def graph(page):
    return page.locator('studio-view').evaluate('editor => window.studioModel.clone(editor.graph)')


def tile(page, node_type):
    return page.locator(f'.studio-library-node[data-node-type="{node_type}"]')


def connected(page):
    add(page, 'Prompt template')
    add(page, 'Report')
    page.locator('studio-view').evaluate('''editor => {
      editor.graph.nodes.forEach((node,index) => editor.graph.layout.nodes[node.id] = {x:40+380*index,y:60}); editor.canvas.paint(); }''')
    page.locator('.studio-inspector').get_by_label('Connect text').select_option(index=1)
    expect(page.locator('.studio-connections path[data-edge-index]')).to_have_count(1)
    return graph(page)


def wire_point(page):
    return page.locator('.studio-connections path[data-edge-index]').first.evaluate('''path => {
      const p = path.getPointAtLength(path.getTotalLength() / 2).matrixTransform(path.getScreenCTM());
      return {x:p.x,y:p.y}; }''')


def drop_at(page, node_type, point):
    viewport = page.locator('.studio-viewport')
    box = viewport.bounding_box()
    tile(page, node_type).drag_to(viewport, target_position={'x': point['x'] - box['x'], 'y': point['y'] - box['y']})


def test_drag_library_places_world_coordinates_and_title_click_does_not_add(placement_page):
    page, _state, errors = placement_page
    assert tile(page, 'prompt').get_by_role('button').evaluate('(e) => getComputedStyle(e).clipPath') == 'inset(50%)'
    tile(page, 'prompt').locator('span').first.click()
    assert graph(page)['nodes'] == []
    page.locator('studio-view').evaluate('''editor => {
      Object.assign(editor.graph.layout.viewport,{x:35,y:20,zoom:.75});editor.canvas.transform();}''')
    box = page.locator('.studio-viewport').bounding_box()
    drop_at(page, 'prompt', {'x': box['x'] + 240, 'y': box['y'] + 180})
    current = graph(page)
    assert len(current['nodes']) == 1
    position = current['layout']['nodes'][current['nodes'][0]['id']]
    assert position['x'] == pytest.approx((240 - 35) / .75, abs=2)
    assert position['y'] == pytest.approx((180 - 20) / .75, abs=2)
    assert not errors


def test_context_menu_search_at_cursor_and_escape(placement_page):
    page, _state, errors = placement_page
    viewport = page.locator('.studio-viewport')
    viewport.click(button='right', position={'x': 210, 'y': 140})
    search = page.get_by_label('Search nodes to place', exact=True)
    search.fill('Text relay')
    page.get_by_role('dialog').get_by_role('button', name='Text relay Tests', exact=True).click()
    current = graph(page)
    assert current['layout']['nodes'][current['nodes'][0]['id']] == {'x': 210, 'y': 140}
    viewport.click(button='right', position={'x': 30, 'y': 30})
    page.get_by_label('Search nodes to place', exact=True).press('Escape')
    expect(page.get_by_role('dialog')).to_have_count(0)
    assert len(graph(page)['nodes']) == 1
    assert not errors


@pytest.mark.parametrize('interaction', ['drop', 'context'])
def test_compatible_edge_insertion_is_one_undo_and_redo(placement_page, interaction):
    page, _state, errors = placement_page
    before = connected(page)
    point = wire_point(page)
    if interaction == 'drop':
        drop_at(page, 'relay', point)
    else:
        page.mouse.click(point['x'], point['y'], button='right')
        page.get_by_label('Search nodes to place', exact=True).fill('Text relay')
        page.get_by_role('dialog').get_by_role('button', name='Text relay Tests', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('inserted')
    current = graph(page)
    relay = next(n for n in current['nodes'] if n['type'] == 'relay')
    assert len(current['edges']) == 2
    assert {e['target']['node'] for e in current['edges']} == {relay['id'], before['edges'][0]['target']['node']}
    page.get_by_role('button', name='Undo', exact=True).click()
    assert graph(page) == before
    page.get_by_role('button', name='Redo', exact=True).click()
    assert graph(page) == current
    assert not errors


def test_incompatible_drop_preserves_original_edge(placement_page):
    page, _state, errors = placement_page
    before = connected(page)
    drop_at(page, 'prompt', wire_point(page))
    expect(page.locator('.studio-status')).to_contain_text('Connection unchanged')
    assert graph(page)['edges'] == before['edges']
    assert len(graph(page)['nodes']) == 3
    assert not errors


def test_ambiguous_ports_require_explicit_choice_and_cancel_adds_nothing(placement_page):
    page, _state, errors = placement_page
    before = connected(page)
    drop_at(page, 'ambiguous', wire_point(page))
    menu = page.get_by_role('dialog', name='Choose connection ports')
    expect(menu).to_be_visible()
    assert graph(page) == before
    menu.get_by_role('button', name='Cancel', exact=True).click()
    assert graph(page) == before
    drop_at(page, 'ambiguous', wire_point(page))
    menu.get_by_role('button', name='second (text) → out (text)', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('inserted')
    assert any(e['target']['port'] == 'second' for e in graph(page)['edges'])
    assert not errors


def test_untrusted_drop_and_keyboard_narrow_add(placement_page):
    page, _state, errors = placement_page
    page.locator('.studio-viewport').evaluate('''viewport => {
      const data = new DataTransfer(); data.setData('application/x-poolhouse-studio-node', JSON.stringify({kind:'node',type:'prompt',version:1,code:'bad'}));
      viewport.dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true}));}''')
    assert graph(page)['nodes'] == []
    page.set_viewport_size({'width': 390, 'height': 844})
    add_button = page.get_by_role('button', name='Add Prompt template', exact=True)
    add_button.focus()
    add_button.press('Enter')
    expect(page.locator('.studio-outline')).to_contain_text('Prompt template')
    assert len(graph(page)['nodes']) == 1
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors


def test_existing_unconnected_node_splices_with_movement_in_same_undo(placement_page):
    page, _state, errors = placement_page
    connected(page)
    add(page, 'Text relay')
    page.locator('studio-view').evaluate('''editor => {const n=editor.graph.nodes.at(-1);
      editor.graph.layout.nodes[n.id]={x:40,y:300};editor.canvas.paint();}''')
    before = graph(page)
    header = page.locator('.studio-node').last.locator('header').bounding_box()
    point = wire_point(page)
    page.mouse.move(header['x'] + 80, header['y'] + 15)
    page.mouse.down()
    page.mouse.move(point['x'], point['y'], steps=8)
    page.mouse.up()
    expect(page.locator('.studio-status')).to_contain_text('inserted')
    assert len(graph(page)['edges']) == 2
    page.get_by_role('button', name='Undo', exact=True).click()
    assert graph(page) == before
    assert not errors


def test_connected_node_input_is_not_stolen_by_dragging_onto_an_edge(placement_page):
    page, _state, errors = placement_page
    connected(page)
    add(page, 'Text relay')
    page.locator('.studio-inspector').get_by_label('Connect in', exact=True).select_option(index=1)
    before = graph(page)
    header = page.locator('.studio-node').last.locator('header').bounding_box()
    point = wire_point(page)
    page.mouse.move(header['x'] + 80, header['y'] + 15)
    page.mouse.down()
    page.mouse.move(point['x'], point['y'], steps=8)
    page.mouse.up()
    expect(page.locator('.studio-status')).to_contain_text('Connection unchanged')
    assert graph(page)['edges'] == before['edges']
    assert not errors


def test_saved_group_drop_is_independent_and_nested_coordinates_are_local(placement_page):
    page, _state, errors = placement_page
    add(page, 'Prompt template')
    page.get_by_role('button', name='Group selection', exact=True).click()
    before = graph(page)
    box = page.locator('.studio-viewport').bounding_box()
    drop_at(page, 'group', {'x': box['x'] + 70, 'y': box['y'] + 350})
    current = graph(page)
    assert len(current['nodes']) == 2
    first, second = current['nodes']
    assert first['group'] != second['group']
    page.get_by_role('button', name='Undo', exact=True).click()
    assert graph(page) == before
    page.get_by_role('button', name='Open group', exact=True).first.click()
    page.locator('studio-view').evaluate('''editor => {
      Object.assign(editor.graph.layout.viewport,{x:20,y:10,zoom:.5});editor.canvas.transform();}''')
    box = page.locator('.studio-viewport').bounding_box()
    drop_at(page, 'relay', {'x': box['x'] + 240, 'y': box['y'] + 200})
    child = graph(page)
    relay = next(n for n in child['nodes'] if n['type'] == 'relay')
    assert child['layout']['nodes'][relay['id']] == pytest.approx({'x': 440, 'y': 380}, abs=2)
    assert not errors


def test_stale_port_choice_cannot_rewrite_a_newer_graph(placement_page):
    page, _state, errors = placement_page
    connected(page)
    drop_at(page, 'ambiguous', wire_point(page))
    page.locator('studio-view').evaluate('editor => {editor.graph.edges=[];editor.changed(false);}')
    page.get_by_role('dialog').get_by_role('button', name='first (text) → out (text)', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('workflow changed')
    assert graph(page)['edges'] == []
    assert len(graph(page)['nodes']) == 2
    assert not errors


def test_context_keyboard_preserves_text_editing_and_traps_menu_focus(placement_page):
    page, _state, errors = placement_page
    add(page, 'Prompt template')
    text = page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)
    text.focus()
    text.press('Shift+F10')
    expect(page.get_by_role('dialog')).to_have_count(0)
    viewport = page.locator('.studio-viewport')
    viewport.focus()
    viewport.press('Shift+F10')
    menu = page.get_by_role('dialog', name='Add node here')
    expect(menu).to_be_visible()
    search = page.get_by_label('Search nodes to place', exact=True)
    search.press('Shift+Tab')
    expect(menu.get_by_role('button', name='Cancel', exact=True)).to_be_focused()
    page.keyboard.press('Tab')
    expect(search).to_be_focused()
    search.press('Escape')
    expect(menu).to_have_count(0)
    expect(viewport).to_be_focused()
    assert not errors


def test_overlapping_connections_preserve_both_and_explain_standalone_drop(placement_page):
    page, _state, errors = placement_page
    connected(page)
    page.locator('studio-view').evaluate('''editor => {
      const s=window.studioModel, [a,b]=editor.graph.nodes, c={...s.clone(a),id:s.id()},d={...s.clone(b),id:s.id()};
      editor.graph.nodes.push(c,d);editor.graph.layout.nodes[c.id]={...editor.graph.layout.nodes[a.id]};
      editor.graph.layout.nodes[d.id]={...editor.graph.layout.nodes[b.id]};
      editor.graph.edges.push({source:{node:c.id,port:'text'},target:{node:d.id,port:'text'}});editor.render();}''')
    expect(page.locator('.studio-connections path[data-edge-index]')).to_have_count(2)
    before = graph(page)['edges']
    drop_at(page, 'relay', wire_point(page))
    expect(page.locator('.studio-status')).to_contain_text('Connections overlap')
    assert graph(page)['edges'] == before
    assert len(graph(page)['nodes']) == 5
    assert not errors


@pytest.mark.parametrize('zoom', [.5, 1.5])
def test_edge_hit_tolerance_is_measured_in_screen_pixels(placement_page, zoom):
    page, _state, errors = placement_page
    connected(page)
    page.locator('studio-view').evaluate('''(editor,zoom) => {
      Object.assign(editor.graph.layout.viewport,{zoom,x:0,y:0});editor.canvas.paint();}''', zoom)
    point = wire_point(page)
    before = graph(page)['edges']
    drop_at(page, 'relay', {'x': point['x'], 'y': point['y'] + 13})
    assert graph(page)['edges'] == before
    assert len(graph(page)['nodes']) == 3
    assert not errors

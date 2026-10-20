"""Insert already-connected nodes without losing fan-out or occupying inputs."""
import pytest
import test_studio_canvas_browser
import test_studio_placement_browser
from browser_expect import expect
from test_studio_canvas_browser import add, studio_browser  # noqa: F401
from test_studio_placement_browser import graph, wire_point

studio_page = test_studio_placement_browser.studio_page
placement_page = test_studio_placement_browser.placement_page
pytestmark = pytest.mark.slow


@pytest.fixture
def connected_page(request, monkeypatch):
    extra = [
        {'type': 'mixer', 'version': 1, 'title': 'Mixer', 'family': 'Tests',
         'inputs': [{'name': 'first', 'type': 'text'}, {'name': 'second', 'type': 'text'}],
         'outputs': [{'name': 'out', 'type': 'text'}, {'name': 'allow', 'type': 'gate'}], 'parameters': {}},
        {'type': 'wide', 'version': 1, 'title': 'Wide mixer', 'family': 'Tests',
         'inputs': [{'name': name, 'type': 'text'} for name in ('first', 'second', 'third')],
         'outputs': [{'name': 'out', 'type': 'text'}], 'parameters': {}},
        {'type': 'gate-relay', 'version': 1, 'title': 'Gate relay', 'family': 'Tests',
         'inputs': [{'name': 'in', 'type': 'text'}],
         'outputs': [{'name': 'out', 'type': 'text'}, {'name': 'allow', 'type': 'gate'}], 'parameters': {}},
    ]
    monkeypatch.setattr(test_studio_canvas_browser, 'SPECS', test_studio_canvas_browser.SPECS + extra)
    return request.getfixturevalue('placement_page')


def build_existing(page, title='Mixer'):
    test_studio_placement_browser.connected(page)
    add(page, title)
    page.locator('.studio-inspector').get_by_label('Connect first', exact=True).select_option(index=1)
    current = graph(page)
    node = current['nodes'][-1]
    add(page, 'Report')
    page.locator('.studio-inspector').get_by_label('Connect text', exact=True).select_option(label=title + ' · out')
    page.locator('studio-view').evaluate('''(editor,id) => {
      editor.graph.layout.nodes[id]={x:40,y:300};editor.canvas.paint();}''', node['id'])
    return node['id']


def drag_existing(page, node_id):
    header = page.locator(f'.studio-node[data-node-id="{node_id}"] header').bounding_box()
    point = wire_point(page)
    hit = page.evaluate('p => document.elementFromPoint(p.x,p.y)?.closest(".studio-node")?.dataset.nodeId', {'x': header['x'] + 80, 'y': header['y'] + 15})
    assert hit == node_id, {'header': header, 'hit': hit, 'target': node_id}
    page.mouse.move(header['x'] + 80, header['y'] + 15)
    page.mouse.down()
    page.mouse.move(point['x'], point['y'], steps=8)
    page.mouse.up()


def test_free_second_input_preserves_fanout_controls_and_atomic_undo(connected_page):
    page, _state, errors = connected_page
    node_id = build_existing(page)
    page.locator('studio-view').evaluate('''(editor,id) => {
      editor.graph.controls.push({source:{node:id,port:'allow'},target:{node:editor.graph.nodes.at(-1).id}});
      editor.canvas.paint();}''', node_id)
    before = graph(page)
    original = before['edges'][0]
    drag_existing(page, node_id)
    expect(page.locator('.studio-status')).to_contain_text('inserted')
    after = graph(page)
    assert after['controls'] == before['controls']
    assert all(edge in after['edges'] for edge in before['edges'][1:])
    assert {'source': original['source'], 'target': {'node': node_id, 'port': 'second'}} in after['edges']
    assert {'source': {'node': node_id, 'port': 'out'}, 'target': original['target']} in after['edges']
    assert len(after['edges']) == len(before['edges']) + 1
    assert after['layout']['nodes'][node_id] != before['layout']['nodes'][node_id]
    page.get_by_role('button', name='Undo', exact=True).click()
    assert graph(page) == before
    page.get_by_role('button', name='Redo', exact=True).click()
    assert graph(page) == after
    assert not errors


def test_occupied_only_inputs_preserve_every_existing_connection(connected_page):
    page, _state, errors = connected_page
    node_id = build_existing(page)
    page.locator(f'.studio-node[data-node-id="{node_id}"]').focus()
    page.keyboard.press('Enter')
    page.locator('.studio-inspector').get_by_label('Connect second', exact=True).select_option(index=1)
    before = graph(page)
    drag_existing(page, node_id)
    expect(page.locator('.studio-status')).to_contain_text('Connection unchanged')
    assert graph(page)['edges'] == before['edges']
    assert graph(page)['controls'] == before['controls']
    assert len(graph(page)['nodes']) == len(before['nodes'])
    assert not errors


@pytest.mark.parametrize('path', ['data', 'control'])
def test_insertion_refuses_new_cycle_through_existing_path(connected_page, path):
    page, _state, errors = connected_page
    add(page, 'Prompt template')
    add(page, 'Gate relay')
    page.locator('.studio-inspector').get_by_label('Connect in', exact=True).select_option(index=1)
    add(page, 'Mixer')
    node_id = graph(page)['nodes'][-1]['id']
    if path == 'data':
        page.locator('.studio-inspector').get_by_label('Connect first', exact=True).select_option(label='Gate relay · out')
    else:
        page.locator('studio-view').evaluate('''editor => {
          editor.graph.controls.push({source:{node:editor.graph.nodes[1].id,port:'allow'},target:{node:editor.graph.nodes[2].id}});}''')
    page.locator('studio-view').evaluate('''editor => {
      editor.graph.nodes.forEach((n,index) => editor.graph.layout.nodes[n.id]=index<2?{x:40+380*index,y:60}:{x:40,y:300});
      editor.canvas.paint();}''')
    before = graph(page)
    drag_existing(page, node_id)
    expect(page.locator('.studio-status')).to_contain_text('acyclic path')
    assert graph(page)['edges'] == before['edges']
    assert graph(page)['controls'] == before['controls']
    assert not errors


def test_multiple_free_inputs_still_require_choice_and_cancel_keeps_wiring(connected_page):
    page, _state, errors = connected_page
    node_id = build_existing(page, 'Wide mixer')
    before = graph(page)
    drag_existing(page, node_id)
    chooser = page.get_by_role('dialog', name='Choose connection ports')
    expect(chooser).to_be_visible()
    assert graph(page)['edges'] == before['edges']
    chooser.get_by_role('button', name='Cancel', exact=True).click()
    assert graph(page)['edges'] == before['edges']
    page.get_by_role('button', name='Undo', exact=True).click()
    assert graph(page) == before
    drag_existing(page, node_id)
    assert chooser.count() == 1, {'status': page.locator('.studio-status').inner_text(), 'graph': graph(page)}
    chooser.get_by_role('button', name='third (text) → out (text)', exact=True).click()
    expect(page.locator('.studio-status')).to_contain_text('inserted')
    after = graph(page)
    assert all(edge in after['edges'] for edge in before['edges'][1:])
    assert any(edge['target'] == {'node': node_id, 'port': 'third'} for edge in after['edges'])
    assert not errors

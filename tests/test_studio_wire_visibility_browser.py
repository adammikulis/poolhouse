"""Real catalogue ports leave a visible, targetable connection between arranged nodes."""
import pytest
import test_studio_canvas_browser
from browser_expect import expect
from test_studio_canvas_browser import add, studio_browser  # noqa: F401

from poolhouse.studio.catalog import node_catalog

studio_page = test_studio_canvas_browser.studio_page
pytestmark = pytest.mark.slow


@pytest.fixture
def catalogue_page(request, monkeypatch):
    monkeypatch.setattr(test_studio_canvas_browser, 'SPECS',
                        [spec for spec in node_catalog() if spec['type'] in {'prompt', 'generate'}])
    return request.getfixturevalue('studio_page')


def test_arranged_real_ports_leave_wire_visible_for_right_click(catalogue_page):
    page, _state, errors = catalogue_page
    add(page, 'Prompt template')
    add(page, 'Generate')
    page.locator('studio-view').evaluate('''editor => {
      editor.graph.nodes.forEach((node,index) => editor.graph.layout.nodes[node.id] = {x:40+300*index,y:60});
      editor.canvas.paint();
    }''')
    page.locator('.studio-inspector').get_by_label('Connect prompt', exact=True).select_option(index=1)
    page.evaluate('() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))')
    point = page.locator('.studio-connections path[data-edge-index]').evaluate('''path => {
      const p = path.getPointAtLength(path.getTotalLength()/2).matrixTransform(path.getScreenCTM());
      return {x:p.x,y:p.y,covered:!!document.elementFromPoint(p.x,p.y)?.closest('.studio-node')};
    }''')
    assert not point['covered'], 'The connection is hidden behind its source node.'
    page.mouse.click(point['x'], point['y'], button='right')
    expect(page.get_by_role('dialog', name='Insert node into connection', exact=True)).to_be_visible()
    assert not errors

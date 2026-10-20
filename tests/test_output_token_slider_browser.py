"""Bounded token selection preserves stored values until explicit interaction."""
import pytest
import test_fleet_simple_controls as controls
from browser_expect import expect
from test_studio_canvas_browser import studio_browser, studio_page  # noqa: F401

browser = controls.browser
daemon = controls.daemon
joined = controls.joined
no_release_lookup = controls.no_release_lookup
open_page = controls.open_page

pytestmark = pytest.mark.slow


def test_context_reduction_preserves_saved_output_until_slider_changes(joined, open_page):
    joined.call('/ui/settings', cookie=joined.cookie, method='POST',
                body={'context': 16384, 'chat_max_output_tokens': 12000})
    page, errors = open_page(joined, cookie=joined.cookie, path='/ui/#settings')
    page.get_by_role('tab', name='Models & providers').click()
    cap = page.get_by_role('slider', name='Default maximum output tokens', exact=True)
    expect(cap).to_have_value('12000')
    context = page.get_by_role('slider', name='Context length', exact=True)
    context.press('ArrowLeft')
    expect(cap).to_have_attribute('max', '8192')
    expect(cap).to_have_value('8192')
    expect(page.locator('settings-view output-token-slider output')).to_have_text('12,000 tokens')
    assert page.locator('settings-view').evaluate('node => node.pick.chat_max_output_tokens') == 12000
    context.press('ArrowRight')
    expect(cap).to_have_value('12000')
    context.press('ArrowLeft')
    page.locator('#settings-save').click()
    expect(page.locator('#settings-note .ok')).to_be_visible()
    _, saved, _ = joined.call('/ui/settings', cookie=joined.cookie)
    assert saved['settings']['chat_max_output_tokens'] == 12000
    assert saved['settings']['context'] == 8192
    cap.press('End')
    cap.press('ArrowLeft')
    expect(cap).to_have_attribute('aria-valuetext', '8,191 tokens')
    page.locator('#settings-save').click()
    expect(page.locator('#settings-note .ok')).to_be_visible()
    _, saved, _ = joined.call('/ui/settings', cookie=joined.cookie)
    assert saved['settings']['chat_max_output_tokens'] == 8191
    assert not errors


def test_studio_generation_control_intersects_schema_and_context_without_writes(joined, open_page):
    page, errors = open_page(joined, cookie=joined.cookie, path='/ui/#studio')
    page.wait_for_function('() => !!window.studioModel')
    page.evaluate('''() => {
      fleetModel.setPreferences({context:4096});
      const host = document.createElement('div'); host.id = 'token-fixture';
      document.body.append(host); window.tokenChanges = [];
      studioModel.field(host, 'max_tokens', {type:'integer',minimum:1,maximum:2048,default:512},
        3000, value => window.tokenChanges.push(value));
    }''')
    cap = page.locator('#token-fixture').get_by_role('slider', name='max_tokens', exact=True)
    expect(cap).to_have_attribute('min', '1')
    expect(cap).to_have_attribute('max', '2048')
    expect(page.locator('#token-fixture output')).to_have_text('3,000 tokens')
    assert page.evaluate('window.tokenChanges') == []
    page.evaluate('fleetModel.setPreferences({context:1024})')
    expect(cap).to_have_attribute('max', '1024')
    expect(page.locator('#token-fixture output')).to_have_text('3,000 tokens')
    assert page.evaluate('window.tokenChanges') == []
    cap.press('Home')
    assert page.evaluate('window.tokenChanges') == [1]
    cap.press('End')
    assert page.evaluate('window.tokenChanges') == [1, 1024]
    page.evaluate('fleetModel.setPreferences({context:"invalid"})')
    expect(cap).to_have_attribute('max', '2048')
    page.locator('#token-fixture').evaluate('node => node.remove()')
    page.evaluate('fleetModel.setPreferences({context:8192})')
    assert page.evaluate('window.tokenChanges') == [1, 1024]
    assert not errors


@pytest.fixture
def generation_page(request, monkeypatch):
    import test_studio_canvas_browser

    from poolhouse.studio.catalog import node_catalog

    monkeypatch.setattr(test_studio_canvas_browser, 'SPECS',
                        [spec for spec in node_catalog() if spec['type'] == 'generate'])
    return request.getfixturevalue('studio_page')


def test_generation_node_uses_slider_in_inspector_and_canvas(generation_page):
    from test_studio_canvas_browser import add

    page, _state, errors = generation_page
    add(page, 'Generate')
    cap = page.locator('.studio-inspector').get_by_role('slider', name='max_tokens', exact=True)
    expect(cap).to_have_attribute('max', '8192')
    expect(page.locator('.studio-node').get_by_role('slider', name='max_tokens', exact=True)).to_be_visible()
    cap.press('Home')
    assert page.locator('studio-view').evaluate('editor => editor.graph.nodes[0].parameters.max_tokens') == 1
    expect(page.locator('.studio-inspector output')).to_have_text('1 token')
    assert not errors

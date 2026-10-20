"""Studio neutral controls retain readable themes, focus and dock navigation."""
import re

import pytest
import test_studio_canvas_browser
from browser_expect import expect
from test_studio_canvas_browser import add, studio_browser  # noqa: F401

from poolhouse.fleet.themes import PRESETS

studio_page = test_studio_canvas_browser.studio_page

pytestmark = pytest.mark.slow


def colors(locator):
    return locator.evaluate('''element => {
      const style = getComputedStyle(element), probe = document.createElement('span');
      element.append(probe);
      const token = name => { probe.style.color = 'var(' + name + ')'; return getComputedStyle(probe).color; };
      const result = {background: style.backgroundColor, text: style.color,
        surface: token('--bg-raised'), sunken: token('--bg-sunken'), ink: token('--text'), muted: token('--muted'),
        border: style.borderColor, outline: style.outlineStyle, outlineColor: style.outlineColor, outlineWidth: style.outlineWidth};
      probe.remove(); return result;
    }''')


def contrast(first, second):
    def luminance(color):
        scale = 1 if color.startswith('color(srgb') else 255
        channels = [float(value) / scale for value in re.findall(r'[\d.]+', color)[:3]]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722), strict=True))
    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + .05) / (dark + .05)



def assert_neutral(control):
    actual = colors(control)
    assert actual['background'] == actual['surface']
    assert actual['text'] == actual['ink']
    assert contrast(actual['text'], actual['background']) >= 4.5
    return actual

@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
def test_studio_neutral_controls_canvas_and_run_dock(studio_page, tmp_path, theme, width):
    page, state, errors = studio_page
    page.set_viewport_size({'width': width, 'height': 1000})
    page.wait_for_function('() => !!window.fleetModel.preferences.resolved_theme')
    page.evaluate('''theme => window.fleetModel.setPreferences({
      ...window.fleetModel.preferences, resolved_theme: theme})''', PRESETS[theme])
    add(page, 'Prompt template')
    add(page, 'Report')
    page.locator('.studio-inspector').get_by_label('Connect text').select_option(index=1)
    library = page.locator('.studio-library').get_by_role('button', name=re.compile('^Add Prompt template'))
    style = assert_neutral(library)
    assert (contrast(style['surface'], 'rgb(0, 0, 0)') > 10) == (theme == 'light')
    primary = colors(page.get_by_role('button', name='Run workflow', exact=True))
    assert primary['background'] == primary['surface']
    assert primary['border'] != style['border']
    assert page.locator('.studio-node header').first.evaluate('(e) => getComputedStyle(e).backgroundColor') == style['sunken']
    wire = page.locator('.studio-connections path').first
    assert wire.evaluate('(e) => getComputedStyle(e).stroke') == style['muted']
    canvas = page.locator('studio-canvas')
    assert contrast(style['muted'], colors(page.locator('.studio-shell'))['background']) >= 3
    library.focus()
    library.press('Tab')
    focused = page.locator(':focus')
    assert focused.evaluate('(e) => e.matches(":focus-visible")')
    assert colors(focused)['outline'] == 'solid'
    assert colors(focused)['outlineWidth'] == '2px'
    assert contrast(colors(focused)['outlineColor'], style['surface']) >= 3
    if width == 390:
        expect(canvas).not_to_be_visible()
        page.locator('.studio-outline').get_by_role('button', name=re.compile('^Prompt template')).click()
        expect(page.locator('.studio-inspector').get_by_label('Prompt text', exact=True)).to_have_value('Hello')
    else:
        expect(canvas).to_be_visible()
        expect(page.locator('.studio-node[aria-selected="true"]')).to_have_count(1)
    page.get_by_role('button', name='Run workflow', exact=True).click()
    expect(page.locator('#studio-runs')).to_contain_text('done')
    node_id = state['saved'][-1]['graph']['nodes'][0]['id']
    state['runs'][0]['nodes'] = {node_id: {'state': 'done', 'outputs': {}}}
    page.route('**/ui/studio/runs/run-1/logs?*', lambda route: route.fulfill(json={'log': 'Fixture worker finished.'}))
    page.get_by_role('button', name='run-1', exact=True).click()
    logs = page.get_by_role('button', name='View logs · Prompt template', exact=True)
    expect(logs).to_be_visible()
    for control in (logs, page.get_by_role('button', name='run-1', exact=True)):
        assert_neutral(control)
    logs.click()
    expect(page.locator('#studio-run-detail')).to_contain_text('Fixture worker finished.')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(tmp_path / f'studio-{theme}-{width}.png'), full_page=True)
    assert not errors

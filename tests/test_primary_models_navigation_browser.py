"""Static branding and direct Models navigation preserve workspace defaults."""
import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401 (fixture)

from poolhouse.fleet.themes import PRESETS

pytestmark = pytest.mark.slow


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('height', [844, 400])
def test_static_brand_and_primary_models_route(studio_browser, tmp_path, theme, width, height):  # noqa: F811
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    with studio_browser() as page:
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            expect(page.locator('studio-view')).to_be_visible()
            expect(page.locator('#app')).to_have_attribute('data-workspace', 'studio')
            page.wait_for_function('() => !!fleetModel.preferences.resolved_theme')
            page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
            brand = page.locator('.poolhouse-mark')
            expect(brand).to_contain_text('Poolhouse')
            expect(brand.locator('svg')).to_be_visible()
            plain = {'background': 'rgba(0, 0, 0, 0)', 'radius': '0px',
                     'border': '0px', 'shadow': 'none', 'cursor': 'auto'}
            appearance = """node => { const style = getComputedStyle(node); return {
                background: style.backgroundColor, radius: style.borderRadius,
                border: style.borderTopWidth, shadow: style.boxShadow, cursor: style.cursor}; }"""
            assert brand.evaluate(appearance) == plain
            brand.hover()
            assert brand.evaluate(appearance) == plain
            assert brand.evaluate("node => node.tagName === 'DIV' && !node.hasAttribute('tabindex') && !node.hasAttribute('role') && !node.hasAttribute('onclick')")
            assert page.get_by_role('link', name='Poolhouse home', exact=True).count() == 0
            primary = page.get_by_role('navigation', name='Main navigation')
            models = primary.get_by_role('link', name='Models', exact=True)
            expect(models).to_be_visible()
            models.focus()
            models.press('Enter')
            expect(page.locator('models-view')).to_be_visible()
            expect(models).to_have_attribute('aria-current', 'page')
            assert page.url.endswith('#models')
            brand.click()
            assert page.url.endswith('#models')
            expect(page.locator('models-view')).to_be_visible()
            expect(page.locator('#nav-context')).to_be_hidden()
            models = primary.get_by_role('link', name='Models', exact=True)
            models.focus()
            models.press('Shift+Tab')
            expect(primary.get_by_role('link', name='Studio', exact=True)).to_be_focused()
            page.keyboard.press('Enter')
            expect(page.locator('studio-view')).to_be_visible()
            assert page.locator('#nav-context a[href="#models"]').count() == 0
            models.click()
            expect(page.locator('models-view')).to_be_visible()
            page.reload()
            expect(primary.get_by_role('link', name='Models', exact=True)).to_have_attribute('aria-current', 'page')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors
        finally:
            served.close()

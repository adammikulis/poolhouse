"""Static branding and direct Models navigation preserve workspace defaults."""
import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401 (fixture)

pytestmark = pytest.mark.slow


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
@pytest.mark.parametrize('height', [844, 400])
def test_static_brand_and_primary_models_route(studio_browser, tmp_path, theme, width, height):  # noqa: F811
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.settings.appearance = {'active_theme': theme, 'saved_themes': []}
    with studio_browser() as page:
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.set_viewport_size({'width': width, 'height': height})
            page.goto(f'http://127.0.0.1:{served.port}/ui/')
            expect(page.locator('studio-view')).to_be_visible()
            expect(page.locator('#app')).to_have_attribute('data-workspace', 'studio')
            expect(page.locator('html')).to_have_attribute('data-theme', theme)
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


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
def test_embedding_family_labels_in_fixture_library(studio_browser, tmp_path, theme, width):  # noqa: F811
    from poolhouse.fleet.catalogue import family_of

    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    served.ui.settings.appearance = {'active_theme': theme, 'saved_themes': []}
    names = ['all-MiniLM-L6-v2', 'all-mpnet-base-v2']
    rows = [{'id': name, 'name': name, 'path': str(tmp_path / name), 'family': family_of(name),
             'format': 'safetensors', 'kind': 'embedding', 'status': 'ready', 'servable': False,
             'shards': 1, 'is_complete': True, 'files': [], 'size_bytes': 90_000_000, 'mtime': 0}
            for name in names]
    with studio_browser() as page:
        try:
            page.set_viewport_size({'width': width, 'height': 900})
            page.route('**/ui/models', lambda route: route.fulfill(json={'ok': True, 'library': rows, 'getting': [], 'unfinished': []}))
            page.goto(f'http://127.0.0.1:{served.port}/ui/#models')
            expect(page.locator('html')).to_have_attribute('data-theme', theme)
            library = page.locator('models-library')
            expect(library.get_by_role('heading', name='MiniLM', exact=True)).to_be_visible()
            expect(library.get_by_role('heading', name='MPNet', exact=True)).to_be_visible()
            expect(library.get_by_role('heading', name='All', exact=True)).to_have_count(0)
            for button in library.get_by_role('button', name='Serve model', exact=True).all():
                expect(button).to_be_disabled()
            library.locator('[data-family=MiniLM]').scroll_into_view_if_needed()
            page.screenshot(path=f'/private/tmp/poolhouse-embedding-family-fixture-{theme}-{width}.png')
        finally:
            served.close()

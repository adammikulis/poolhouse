"""Operational pages share spacing, readable navigation and accessible route headings."""
import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401 (fixture)

from poolhouse.fleet.themes import PRESETS

pytestmark = pytest.mark.slow


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1600, 390])
def test_operational_pages_share_shell_without_redundant_titles(tmp_path, studio_browser, theme, width):  # noqa: F811
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    with studio_browser() as page:
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.set_viewport_size({'width': width, 'height': 1000})
            page.goto(f'http://127.0.0.1:{served.port}/ui/#tools')
            page.wait_for_function('() => !!fleetModel.preferences.resolved_theme')
            page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
            nav = page.get_by_role('navigation', name='Main navigation')
            nav.get_by_role('link', name='Work', exact=True).click()
            gaps = []
            for route, label, heading in [('tools', 'Tools', 'Tools'), ('knowledge', 'Graph', 'Graph inspector'),
                                          ('history', 'Activity', 'Activity'), ('projects', 'Projects', 'Projects'),
                                          ('tasks', 'Tasks', 'Tasks')]:
                page.get_by_role('navigation', name='Workspace pages').get_by_role('link', name=label, exact=True).click()
                shell = page.locator(f'{route}-view .page-shell')
                expect(shell).to_be_visible()
                expect(page.get_by_role('heading', name=heading, exact=True, level=1)).to_have_count(1)
                title = shell.locator('.page-heading h1')
                assert title.evaluate('node => getComputedStyle(node).clipPath') == 'inset(50%)'
                assert title.bounding_box()['height'] == 1
                gaps.append(shell.evaluate('node => getComputedStyle(node).paddingLeft'))
                assert page.locator('#workspace').evaluate('node => getComputedStyle(node).paddingLeft') == '0px'
                assert shell.locator('h2').count() > 0
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            nav.get_by_role('link', name='Pool', exact=True).click()
            pool = page.locator('pool-view .page-shell')
            expect(pool).to_be_visible()
            gaps.append(pool.evaluate('node => getComputedStyle(node).paddingLeft'))
            assert len(set(gaps)) == 1
            page.wait_for_function('''() => { const label = document.querySelector('.rail-link span');
                return label && parseFloat(getComputedStyle(label).fontSize) >= 11; }''')
            page.wait_for_function('''() => { const link = document.querySelector('.context-link');
                return link && parseFloat(getComputedStyle(link).fontSize) >= 13; }''')
            assert page.locator('.rail-link').evaluate_all('''nodes => nodes.every(node => {
                const box = node.getBoundingClientRect();
                return [...node.querySelectorAll('span')].every(label => {
                    const text = label.getBoundingClientRect();
                    return !text.width || text.left >= box.left && text.right <= box.right;
                });
            })''')
            assert not errors
        finally:
            served.close()


def test_navigation_labels_and_short_window_keyboard_reachability(tmp_path, studio_browser):  # noqa: F811
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    with studio_browser() as page:
        try:
            page.set_viewport_size({'width': 780, 'height': 420})
            page.goto(f'http://127.0.0.1:{served.port}/ui/#tools')
            nav = page.get_by_role('navigation', name='Main navigation')
            nav.get_by_role('link', name='Studio', exact=True).click()
            pages = page.get_by_role('navigation', name='Workspace pages')
            for label in ['Training', 'Gym', 'Evaluation', 'Datasets']:
                expect(pages.get_by_role('link', name=label, exact=True)).to_be_visible()
            expect(page.locator('#nav-jobs')).to_have_attribute('aria-label', 'Jobs: 0 running · 0 queued')
            first = nav.get_by_role('link', name='Conversations', exact=True)
            first.focus()
            page.evaluate('''() => { const focused=document.activeElement;
                document.querySelector('fleet-nav').paint(fleetModel.route);
                if (document.activeElement !== focused) throw Error('Navigation refresh lost focus'); }''')
            for _ in range(20):
                if page.locator('#nav-settings').evaluate('node => node === document.activeElement'):
                    break
                page.keyboard.press('Tab')
            expect(page.locator('#nav-settings')).to_be_focused()
            bounds = page.locator('#nav-settings').bounding_box()
            assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 420
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            served.close()

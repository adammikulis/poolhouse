"""Models summaries and pending Settings preferences reflect actual known state."""
import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401 (fixture)

from poolhouse.fleet.themes import PRESETS

pytestmark = pytest.mark.slow


@pytest.fixture
def feedback_page(tmp_path, studio_browser):  # noqa: F811
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    with studio_browser() as page:
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            yield served, page, errors
        finally:
            served.close()


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
def test_models_count_chat_capability_and_show_known_storage(feedback_page, theme, width):
    served, page, errors = feedback_page
    state = {'free_gb': None, 'runtime_ready': True, 'available': True}
    models = [
        {'model': 'Harbor', 'path': '/fixture/harbor', 'capabilities': {'chat': True}},
        {'model': 'Harbor', 'path': '/fixture/harbor', 'capabilities': {'chat': True}},
        {'model': 'Cedar', 'capabilities': {'chat': True}},
        {'model': 'Harbor', 'member': 'fixture-device', 'capabilities': {'chat': True}},
        {'model': 'Embedding', 'capabilities': {'chat': False}},
        {'model': 'Unverified', 'capabilities': {'chat': None}}]
    page.route('**/ui/models', lambda route: route.fulfill(status=200 if state['available'] else 503,
        json={'library': [], 'free_gb': state['free_gb']} if state['available'] else {'error': 'Inventory unavailable'}))
    page.route('**/ui/chat', lambda route: route.fulfill(status=200 if state['available'] else 503,
        json={'models': models, 'runtime_ready': state['runtime_ready']}))
    page.route('**/ui/serving', lambda route: route.fulfill(json={'running': [
        {'models': ['Embedding'], 'capabilities': {'chat': False}},
        {'models': ['Guard'], 'capabilities': {'chat': False}},
        {'models': ['Harbor', 'Cedar'], 'capabilities': {'chat': True}},
        {'models': ['Unverified'], 'capabilities': {'chat': None}}]}))
    page.route('**/ui/fleet', lambda route: route.fulfill(json={'members': [
        {'name': 'fixture-device', 'is_self': True, 'device': {}, 'slots': 1, 'free': 1}]}))
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto(f'http://127.0.0.1:{served.port}/ui/#models')
    page.wait_for_function('() => !!fleetModel.preferences.resolved_theme')
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
    expect(page.locator('#models-running')).to_have_text('3')
    expect(page.locator('#models-free')).to_have_text('Unavailable')
    primary = page.get_by_role('navigation', name='Main navigation')
    primary.get_by_role('link', name='Pool', exact=True).click()
    action = page.locator('.loaded-models').get_by_role('button', name='Open Models', exact=True)
    expect(action).to_be_visible()
    state['free_gb'], state['runtime_ready'] = 0, False
    action.click()
    expect(page.locator('#models-running')).to_have_text('0')
    expect(page.locator('#models-free')).to_have_text('0 GiB')
    state['available'] = False
    primary.get_by_role('link', name='Pool', exact=True).click()
    page.locator('.loaded-models').get_by_role('button', name='Open Models', exact=True).click()
    expect(page.locator('#models-count')).to_have_text('Unavailable')
    expect(page.locator('#models-running')).to_have_text('Unavailable')
    expect(page.locator('#models-free')).to_have_text('Unavailable')
    assert not errors

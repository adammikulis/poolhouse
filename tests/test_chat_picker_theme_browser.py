"""Model choice names remain readable in both application themes."""
import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401 (fixture)
from test_studio_theme_browser import contrast

from poolhouse.fleet.themes import PRESETS

pytestmark = pytest.mark.slow


@pytest.fixture
def model_choices(tmp_path, studio_browser):  # noqa: F811
    served = Serving(tmp_path)
    served.ui.settings.setup_done = True
    with studio_browser() as page:
        page.route('**/ui/chat', lambda route: route.fulfill(json={'models': [
            {'model': 'Harbor model', 'path': 'fixtures/harbor', 'local': True},
            {'model': 'Cedar model', 'path': 'fixtures/cedar', 'local': True}]}))
        page.route('**/ui/models', lambda route: route.fulfill(json={'ok': True, 'library': [
            {'name': 'Harbor model', 'path': 'fixtures/harbor', 'family': 'Fixture models', 'servable': True},
            {'name': 'Cedar model', 'path': 'fixtures/cedar', 'family': 'Fixture models', 'servable': True},
            {'name': 'Maple model', 'path': 'fixtures/maple', 'family': 'Fixture models', 'servable': True}]}))
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(f'http://127.0.0.1:{served.port}/ui#chat')
            expect(page.locator('#chat-model-button')).to_be_visible()
            page.wait_for_function('() => !!window.fleetModel.preferences.resolved_theme')
            yield page, errors
        finally:
            served.close()


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('width', [1440, 390])
def test_model_names_and_statuses_have_contrast_and_keyboard_selection(model_choices, tmp_path, theme, width):
    page, errors = model_choices
    page.set_viewport_size({'width': width, 'height': 1000})
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences, resolved_theme: theme})', PRESETS[theme])
    page.locator('#chat-model-button').click()
    choices = page.locator('#chat-model-list .chat-model-choice')
    expect(choices).to_have_count(3)
    observed = choices.evaluate_all('''rows => rows.map(row => {
      const style=getComputedStyle(row);
      return {name:row.querySelector('span').textContent, background:style.backgroundColor,
        nameColor:getComputedStyle(row.querySelector('span')).color,
        statuses:[...row.querySelectorAll('small')].map(node=>getComputedStyle(node).color)};
    })''')
    page.screenshot(path=str(tmp_path / f'chat-picker-{theme}-{width}.png'), full_page=True)
    assert (contrast(observed[0]['background'], 'rgb(0, 0, 0)') > 10) == (theme == 'light')
    expect(page.locator('.chat-model-choice[aria-pressed=true]')).to_have_count(1)
    for row in observed:
        assert contrast(row['nameColor'], row['background']) >= 4.5, row
        for status in row['statuses']:
            assert contrast(status, row['background']) >= 4.5, row
    search = page.locator('#chat-model-dialog').get_by_label('Search models', exact=True)
    search.fill('Cedar')
    expect(choices).to_have_count(1)
    search.press('Tab')
    expect(choices.first).to_be_focused()
    choices.first.press('Enter')
    expect(page.locator('#chat-model-dialog')).not_to_be_visible()
    expect(page.locator('#chat-model-button')).to_have_text('Cedar model')
    assert not errors

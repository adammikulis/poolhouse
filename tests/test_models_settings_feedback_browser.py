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


def open_preferences(served, page):
    page.goto(f'http://127.0.0.1:{served.port}/ui/#settings')
    expect(page.locator('#settings-save')).to_be_enabled()
    expect(page.get_by_label('Studio undo steps', exact=True)).to_have_value('32')


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
def test_settings_responsive_tabs_and_drafts_survive_navigation(feedback_page, theme, width):
    served, page, errors = feedback_page
    page.route('**/ui/libraries', lambda route: route.fulfill(json={'ready': False, 'libraries': [
        {'name': 'fixture-library', 'title': 'Fixture library', 'installed': False}]}))
    page.set_viewport_size({'width': width, 'height': 1000})
    open_preferences(served, page)
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
    tabs = page.get_by_role('tablist', name='Settings sections')
    expect(tabs).to_have_attribute('aria-orientation', 'horizontal' if width == 390 else 'vertical')
    appearance = tabs.get_by_role('tab', name='Appearance & advanced', exact=True)
    appearance.focus()
    appearance.press('ArrowRight' if width == 390 else 'ArrowDown')
    expect(tabs.get_by_role('tab', name='Models & providers', exact=True)).to_be_focused()
    page.keyboard.press('Home')
    expect(appearance).to_be_focused()
    undo = page.get_by_label('Studio undo steps', exact=True)
    undo.fill('44')
    expect(page.locator('#settings-dirty')).to_have_text('Unsaved preferences')
    libraries = tabs.get_by_role('tab', name='Libraries & simulators', exact=True)
    libraries.click()
    page.locator('#lib-fixture-library').check()
    expect(page.locator('#settings-save')).to_be_visible()
    expect(page.locator('.settings-footer')).to_contain_text('controls save separately')
    page.get_by_role('navigation', name='Main navigation').get_by_role('link', name='Studio', exact=True).click()
    page.get_by_role('link', name='Settings', exact=True).click()
    expect(page.locator('#lib-fixture-library')).to_be_checked()
    expect(page.locator('#settings-dirty')).to_have_text('Unsaved preferences')
    page.get_by_role('button', name='Discard preferences', exact=True).click()
    expect(libraries).to_be_focused()
    expect(page.locator('#settings-dirty')).to_have_text('No unsaved preferences')
    expect(page.locator('#lib-fixture-library')).to_be_checked()
    appearance.click()
    expect(undo).to_have_value('32')
    page.set_viewport_size({'width': 1440 if width == 390 else 390, 'height': 1000})
    expect(tabs).to_have_attribute('aria-orientation', 'vertical' if width == 390 else 'horizontal')
    appearance.focus()
    appearance.press('ArrowDown' if width == 390 else 'ArrowRight')
    expect(tabs.get_by_role('tab', name='Models & providers', exact=True)).to_be_focused()
    page.keyboard.press('Tab')
    expect(page.locator('#download_sources-both')).to_be_focused()
    appearance.click()
    last = page.locator('#settings-tokens output-token-slider').get_by_role('slider')
    last.focus()
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    field, footer = last.bounding_box(), page.locator('.settings-footer').bounding_box()
    assert field['y'] >= 0 and field['y'] + field['height'] <= footer['y']
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors


def test_settings_pending_save_keeps_newer_edits_and_uses_submitted_snapshot(feedback_page):
    served, page, errors = feedback_page
    open_preferences(served, page)
    held = []

    def delayed(route):
        if route.request.method != 'POST':
            route.continue_()
            return
        response = route.fetch()
        held.append((route, response, route.request.post_data_json))

    page.route('**/ui/settings', delayed)
    undo = page.get_by_label('Studio undo steps', exact=True)
    undo.fill('44')
    page.get_by_role('button', name='Save preferences', exact=True).click()
    expect(page.get_by_role('button', name='Saving…', exact=True)).to_be_disabled()
    undo.fill('64')
    page.get_by_role('tab', name='Libraries & simulators', exact=True).click()
    assert held[0][2]['studio_undo_steps'] == 44
    held[0][0].fulfill(response=held[0][1])
    expect(page.locator('#settings-note')).to_contain_text('Newer edits remain unsaved')
    expect(page.locator('#settings-dirty')).to_have_text('Unsaved preferences')
    assert page.evaluate('fleetModel.preferences.studio_undo_steps') == 44
    assert served.ui.settings.studio_undo_steps == 44
    page.get_by_role('tab', name='Appearance & advanced', exact=True).click()
    expect(undo).to_have_value('64')
    page.unroute('**/ui/settings', delayed)
    page.get_by_role('tab', name='Libraries & simulators', exact=True).click()
    page.get_by_role('button', name='Save preferences', exact=True).click()
    expect(page.locator('#settings-dirty')).to_have_text('No unsaved preferences')
    assert served.ui.settings.studio_undo_steps == 64
    assert not errors


def test_settings_failed_save_preserves_dirty_draft_for_explicit_retry(feedback_page):
    served, page, errors = feedback_page
    open_preferences(served, page)
    page.route('**/ui/settings', lambda route: route.fulfill(status=400, json={'error': 'Preferences are read-only'})
               if route.request.method == 'POST' else route.continue_())
    page.get_by_label('Studio undo steps', exact=True).fill('72')
    page.get_by_role('button', name='Save preferences', exact=True).click()
    expect(page.locator('#settings-note .err')).to_have_text('Preferences are read-only')
    expect(page.locator('#settings-dirty')).to_have_text('Unsaved preferences')
    page.get_by_role('navigation', name='Main navigation').get_by_role('link', name='Studio', exact=True).click()
    page.get_by_role('link', name='Settings', exact=True).click()
    expect(page.get_by_label('Studio undo steps', exact=True)).to_have_value('72')
    page.unroute('**/ui/settings')
    page.get_by_role('button', name='Save preferences', exact=True).click()
    expect(page.locator('#settings-dirty')).to_have_text('No unsaved preferences')
    assert served.ui.settings.studio_undo_steps == 72
    assert not errors


def test_settings_discard_restores_radios_context_and_uncapped_output(feedback_page):
    served, page, errors = feedback_page
    served.ui.settings.context = 16384
    served.ui.settings.chat_max_output_tokens = None
    open_preferences(served, page)
    page.get_by_role('tab', name='Compute & device', exact=True).click()
    page.get_by_role('radio', name='Quit', exact=False).check()
    page.get_by_role('tab', name='Models & providers', exact=True).click()
    page.locator('#download_sources-internet').check()
    page.get_by_role('tab', name='Appearance & advanced', exact=True).click()
    context = page.locator('#settings-tokens #ctx')
    context.get_by_role('slider').focus()
    context.get_by_role('slider').press('ArrowRight')
    output = page.locator('#settings-tokens output-token-slider')
    output.get_by_role('slider').focus()
    output.get_by_role('slider').press('ArrowRight')
    expect(page.locator('#settings-dirty')).to_have_text('Unsaved preferences')
    page.get_by_role('button', name='Discard preferences', exact=True).click()
    page.get_by_role('tab', name='Models & providers', exact=True).click()
    expect(page.locator('#download_sources-both')).to_be_checked()
    page.get_by_role('tab', name='Appearance & advanced', exact=True).click()
    expect(context.locator('output')).to_have_text('16,384 tokens')
    assert output.evaluate('node => node.value') == ''
    page.get_by_role('tab', name='Compute & device', exact=True).click()
    expect(page.locator('input[name=on_close][value=""]')).to_be_checked()
    expect(page.locator('#settings-dirty')).to_have_text('No unsaved preferences')
    assert not errors


def test_settings_auth_loss_rejects_old_save_completion(feedback_page):
    served, page, errors = feedback_page
    open_preferences(served, page)
    expect(page.locator('theme-editor .theme-presets button')).to_have_count(2)
    held = []

    def delayed(route):
        if route.request.method == 'POST':
            held.append((route, route.fetch()))
        else:
            route.continue_()

    page.route('**/ui/settings', delayed)
    page.get_by_label('Studio undo steps', exact=True).fill('44')
    page.get_by_role('button', name='Save preferences', exact=True).click()
    expect(page.get_by_role('button', name='Saving…', exact=True)).to_be_disabled()
    page.get_by_role('button', name='Sign out', exact=True).click()
    page.wait_for_function("() => ['sign-in','launch-needed'].includes(fleetModel.route)")
    held[0][0].fulfill(response=held[0][1])
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert page.evaluate("document.querySelector('settings-view').savedPick") is None
    assert page.evaluate('fleetModel.preferences.studio_undo_steps') == 32
    expect(page.locator('#settings-save')).to_have_text('Save preferences')
    assert not errors


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [1440, 390])
def test_settings_token_controls_share_chrome_and_lead_preferences(feedback_page, theme, width):
    served, page, errors = feedback_page
    page.set_viewport_size({'width': width, 'height': 1000})
    open_preferences(served, page)
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences,resolved_theme:theme})', PRESETS[theme])
    context = page.get_by_role('slider', name='Context length', exact=True)
    output = page.get_by_role('slider', name='Default maximum output tokens', exact=True)
    expect(context).to_be_visible()
    expect(output).to_be_visible()
    positions = page.locator('#settings-tokens').evaluate("""node => {
      const context = node.querySelector('#ctx'), output = node.querySelector('output-token-slider ml-slider');
      const chrome = slider => {
        const input = slider.shadowRoot.querySelector('input');
        const css = getComputedStyle(input);
        return {height:css.height,accent:css.accentColor,margin:css.margin, label:!!slider.shadowRoot.querySelector('.top label'), value:!!slider.shadowRoot.querySelector('.top output')};
      };
      return {first:node.parentElement.firstElementChild===node,context:context.getBoundingClientRect().top,
        output:output.getBoundingClientRect().top,theme:node.parentElement.querySelector('theme-editor').getBoundingClientRect().top,
        contextChrome:chrome(context),outputChrome:chrome(output)};
    }""")
    assert positions['first']
    assert positions['context'] < positions['output'] < positions['theme']
    assert positions['contextChrome'] == positions['outputChrome']
    context.press('ArrowRight')
    assert output.evaluate('node => node.getRootNode().host.stops.at(-1)') == 16384
    output.press('End')
    expect(output).to_have_attribute('aria-valuetext', '16,384 tokens')
    submitted = []
    page.on('request', lambda request: submitted.append(request.post_data_json)
            if request.method == 'POST' and request.url.endswith('/ui/settings') else None)
    page.get_by_role('button', name='Save preferences', exact=True).click()
    expect(page.locator('#settings-note .ok')).to_be_visible()
    assert served.ui.settings.context == 16384
    assert served.ui.settings.chat_max_output_tokens == 16384
    assert len(submitted) == 1 and 'labels' not in submitted[0]
    page.get_by_role('tab', name='Compute & device', exact=True).click()
    expect(page.get_by_text('What this device does', exact=True)).to_have_count(0)
    expect(page.locator('input[name=labels]')).to_have_count(0)
    assert page.locator('settings-view').evaluate('node => !("labels" in node.pick)')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors

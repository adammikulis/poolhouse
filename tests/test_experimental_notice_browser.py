"""Explicit product acknowledgement before fresh setup or an existing workspace."""
import pytest
from browser_expect import expect
from test_experimental_notice import NoticeServing
from test_studio_canvas_browser import studio_browser  # noqa: F401

from poolhouse.fleet import notice_routes, settings
from poolhouse.fleet.settings import Settings

pytestmark = pytest.mark.slow


def capture(page, name):
    page.screenshot(path=f'/tmp/poolhouse-notice-fe3dd5-{name}.png', full_page=False)


@pytest.fixture
def notice_page(tmp_path, request):
    opened = request.getfixturevalue('studio_browser')
    served = NoticeServing(tmp_path)
    try:
        with opened() as page:
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            yield page, served, errors
    finally:
        served.close()


def test_fresh_notice_requires_explicit_keyboard_accept_before_wizard_and_persists(notice_page):
    page, served, errors = notice_page
    page.goto(f'http://127.0.0.1:{served.port}/ui/#studio')
    notice = page.locator('experimental-notice')
    expect(notice.get_by_role('heading', name='Poolhouse is experimental')).to_be_visible()
    capture(page, 'fresh-focused')
    actual_version = served.call('/ui/setup')[1]['version'] or 'version unavailable'
    expect(page.locator('.version-footer')).to_have_text('Poolhouse ' + actual_version)
    page.keyboard.press('Escape')
    page.evaluate("location.hash = '#gym'")
    expect(notice.get_by_role('button', name='Accept and continue')).to_be_visible()
    expect(page.locator('#app')).to_be_hidden()
    assert Settings.load(served.ui.settings_path).experimental_notice_accepted is False
    notice.get_by_role('button', name='Accept and continue').focus()
    page.keyboard.press('Enter')
    expect(page.locator('first-run #first-run')).to_be_visible()
    capture(page, 'accepted-wizard')
    assert Settings.load(served.ui.settings_path).experimental_notice_accepted is True
    page.reload()
    expect(page.locator('first-run #first-run')).to_be_visible()
    expect(notice.locator('#experimental-notice')).to_be_hidden()
    served.ui.settings.setup_done = True
    served.ui.settings.save(served.ui.settings_path)
    served.ui.settings = Settings.load(served.ui.settings_path)
    page.goto(f'http://127.0.0.1:{served.port}/ui/#chat')
    expect(page.locator('#app')).to_be_visible()
    expect(notice.locator('#experimental-notice')).to_be_hidden()
    assert not errors


def test_failed_save_keeps_existing_install_gated_until_deliberate_retry(notice_page, monkeypatch):
    page, served, errors = notice_page
    served.ui.settings.setup_done = True
    served.ui.settings.save(served.ui.settings_path)
    original = settings.write_json
    monkeypatch.setattr(settings, 'write_json', lambda *_: (_ for _ in ()).throw(OSError('test disk refusal')))
    page.goto(f'http://127.0.0.1:{served.port}/ui/#chat')
    notice = page.locator('experimental-notice')
    notice.get_by_role('button', name='Accept and continue').click()
    expect(notice.get_by_role('status')).to_contain_text('could not be saved')
    capture(page, 'save-error')
    expect(page.locator('#app')).to_be_hidden()
    assert Settings.load(served.ui.settings_path).experimental_notice_accepted is False
    monkeypatch.setattr(settings, 'write_json', original)
    notice.get_by_role('button', name='Accept and continue').click()
    expect(page.locator('#app')).to_be_visible()
    capture(page, 'accepted-workspace')
    assert Settings.load(served.ui.settings_path).experimental_notice_accepted is True
    assert not errors


def test_stale_accept_response_cannot_reopen_workspace_after_signout(notice_page):
    page, served, errors = notice_page
    served.ui.settings.setup_done = True
    served.ui.settings.save(served.ui.settings_path)
    def hold_response(route):
        response = route.fetch()
        page.evaluate('() => window.fleetModel.signOut()')
        route.fulfill(response=response)
    page.route('**/ui/notice/accept', hold_response)
    page.goto(f'http://127.0.0.1:{served.port}/ui/#chat')
    page.get_by_role('button', name='Accept and continue').click()
    expect(page.locator('#app')).to_be_hidden()
    page.wait_for_function("() => ['launch-needed', 'sign-in'].includes(window.fleetModel.route)")
    assert page.evaluate('window.fleetModel.noticeAccepted') is False
    capture(page, 'stale-signout')
    assert not errors


def test_footer_both_themes_narrow_text_zoom_and_workspace_bottom_space(notice_page):
    page, served, errors = notice_page
    served.ui.settings.setup_done = True
    served.ui.settings.save(served.ui.settings_path)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(f'http://127.0.0.1:{served.port}/ui/#chat')
    footer = page.locator('.version-footer')
    for theme in ('light', 'dark'):
        page.wait_for_function('() => window.fleetModel.preferences.resolved_theme')
        page.evaluate('(theme) => { const M=window.fleetModel; M.preferences.resolved_theme={base:theme,font_size:28}; M.applyTheme(); }', theme)
        page.wait_for_function('(theme) => document.documentElement.dataset.theme === theme', arg=theme)
        expect(footer).to_be_visible()
        capture(page, 'narrow-' + theme)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert footer.bounding_box()['y'] + footer.bounding_box()['height'] <= 844
    page.get_by_role('button', name='Accept and continue').click()
    expect(page.locator('#app')).to_be_visible()
    assert page.locator('.chatmain').bounding_box()['y'] + page.locator('.chatmain').bounding_box()['height'] <= footer.bounding_box()['y'] + 1
    page.evaluate("window.fleetModel.go('studio')")
    expect(page.locator('.studio-workspace')).to_be_visible()
    workspace = page.locator('.studio-workspace').bounding_box()
    assert workspace['y'] + workspace['height'] <= footer.bounding_box()['y'] + 1
    capture(page, 'narrow-studio')
    assert not errors


def test_bug_report_opens_fixed_external_browser_without_navigation_or_acknowledgement(notice_page, monkeypatch):
    page, served, errors = notice_page
    opened = []
    monkeypatch.setattr(notice_routes.platform, 'open_path', lambda value: opened.append(value) or 'open')
    page.goto(f'http://127.0.0.1:{served.port}/ui/#chat')
    before = page.url
    page.get_by_role('link', name='Report bugs on GitHub').click()
    page.wait_for_function("() => !document.querySelector('#experimental-accept').disabled")
    assert opened == [notice_routes.GIT_URL + '/issues']
    assert page.url == before and not served.ui.settings.experimental_notice_accepted
    expect(page.get_by_role('button', name='Accept and continue')).to_be_visible()
    assert not errors


@pytest.mark.parametrize('calculation_only', [False, True])
def test_full_fit_route_gates_but_standalone_calculator_only_shows_advisory(notice_page, calculation_only):
    from poolhouse.fleet.page import FIT_ONLY

    page, served, errors = notice_page
    if calculation_only:
        served.ui.parts = FIT_ONLY
    page.goto(f'http://127.0.0.1:{served.port}/ui/fit')
    expect(page.locator('.version-footer')).to_be_visible()
    if calculation_only:
        advisory = page.get_by_role('complementary', name='Experimental software')
        expect(advisory).to_contain_text('AI agents can be unpredictable')
        expect(advisory).to_contain_text('accept no liability')
        capture(page, 'calculator-advisory')
        expect(page.get_by_role('button', name='Accept and continue')).to_have_count(0)
    else:
        expect(page.get_by_role('button', name='Accept and continue')).to_be_visible()
        expect(page.locator('#app')).to_be_hidden()
    assert not Settings.load(served.ui.settings_path).experimental_notice_accepted
    assert not errors


def test_fullscreen_gym_keeps_same_footer_and_restores_it(notice_page):
    page, served, errors = notice_page
    served.ui.settings.setup_done = True
    served.ui.settings.experimental_notice_accepted = True
    served.ui.settings.save(served.ui.settings_path)
    page.goto(f'http://127.0.0.1:{served.port}/ui/#gym')
    # Empty-session controls intentionally disable Expand; exercise the actual
    # owned stage fullscreen API without creating a simulator or compute job.
    page.locator('gym-view .gym-stage').evaluate('(stage) => stage.addEventListener("click", () => stage.requestFullscreen(), {once:true})')
    page.locator('gym-view .gym-stage').click()
    page.wait_for_function('() => document.fullscreenElement === document.querySelector("gym-view .gym-stage")')
    expect(page.locator('.gym-stage .version-footer')).to_be_visible()
    assert page.locator('gym-view gym-scene').evaluate('(scene) => parseFloat(getComputedStyle(scene).height)') <= page.locator('.version-footer').bounding_box()['y'] + 1
    capture(page, 'fullscreen-gym')
    page.evaluate('document.exitFullscreen()')
    page.wait_for_function('() => !document.fullscreenElement')
    expect(page.locator('#root > .version-footer')).to_be_visible()
    expect(page.locator('.version-footer')).to_have_count(1)
    capture(page, 'fullscreen-restored')
    assert not errors

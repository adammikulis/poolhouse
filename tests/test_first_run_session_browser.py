"""Real temporary setup sessions recover through a test-double native holder, without replay."""
import pytest
from test_fleet_bootstrap import browser, device
from test_fleet_initial_setup import initial

__all__ = ['browser', 'device']
pytestmark = [pytest.mark.slow, pytest.mark.redteam]


def test_expired_first_run_session_renews_once_and_never_replays_name_change(device, browser, tmp_path):
    assert initial(device)[0] == 200
    page = browser.new_page()
    page.set_default_timeout(5000)
    page.expose_function('nativeTicket', lambda: device.ui.sessions.mint_ticket('launch-secret')[0])
    page.add_init_script('''window.nativeCalls=0;window.__TAURI__={core:{invoke:async command=>{
      if(command!=="renew_session")throw Error("Unexpected native command");
      nativeCalls++;window.mintPending=true;await new Promise(resolve=>window.releaseMint=resolve);
      return nativeTicket();}}};''')
    sent = []
    page.on('request', lambda request: sent.append(request.post_data_json)
            if request.method == 'POST' and request.url.endswith('/ui/setup/initial') else None)
    page.goto(f'http://127.0.0.1:{device.port}/ui/')
    page.get_by_role('heading', name='Welcome to Poolhouse', exact=True).wait_for()
    page.locator('#setup-device-name').fill('unsent-new-name')
    with device.ui.sessions._lock:
        for session in device.ui.sessions._sessions.values():
            session.expires_at = 0
    page.get_by_role('button', name='Continue', exact=True).click()
    page.wait_for_function('() => window.mintPending === true')
    page.screenshot(path=str(tmp_path / '01-current-session-renewing.png'), full_page=True)
    assert page.evaluate('nativeCalls') == 1
    assert device.ui.name == 'quillhaven'
    assert len(sent) == 1
    page.evaluate('window.releaseMint()')
    page.get_by_role('heading', name='Welcome to Poolhouse', exact=True).wait_for()
    assert page.evaluate('nativeCalls') == 1
    assert len(sent) == 1
    assert page.evaluate('async() => (await fleetModel.api("/ui/session")).signed_in') is True
    assert page.get_by_role('button', name='Start chat', exact=True).count() == 0
    assert page.get_by_role('button', name='Continue', exact=True).is_enabled()
    page.screenshot(path=str(tmp_path / '02-recovered-explicit-action.png'), full_page=True)
    page.close()


def test_old_setup_401_cannot_refuse_or_replay_in_a_new_session(browser, tmp_path):
    import json

    from test_startup_model_page import SetupPage

    setup, held = SetupPage(), []
    def route(request):
        if request.request.url.endswith('/ui/setup/prefs'):
            setup.posted.append(('/ui/setup/prefs', request.request.post_data_json))
            held.append(request)
        else:
            setup.route(request)
    page = browser.new_page()
    page.set_default_timeout(5000)
    page.route('http://127.0.0.1:8765/**', route)
    page.goto('http://127.0.0.1:8765/')
    page.get_by_role('button', name='Continue', exact=True).click()
    page.locator('#startup-model-choice').wait_for()
    page.get_by_role('button', name='Prepare and continue', exact=True).click()
    page.wait_for_function('() => document.querySelector("first-run").busy !== null')
    assert len(held) == 1
    page.evaluate('async () => { await fleetModel.signOut(); fleetModel.go("first-run"); }')
    page.get_by_role('button', name='Prepare and continue', exact=True).wait_for()
    generation = page.evaluate('fleetModel.sessionContext()')
    with page.expect_response(lambda response: response.url.endswith('/ui/setup/prefs')) as reply:
        held[0].fulfill(status=401, body=json.dumps({'error':'Old window ended.'}), content_type='application/json')
    reply.value.finished()
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert page.evaluate('fleetModel.sessionContext()') == generation
    assert page.evaluate('fleetModel.route') == 'first-run'
    assert page.get_by_role('button', name='Prepare and continue', exact=True).is_enabled()
    assert len([1 for path, _ in setup.posted if path == '/ui/setup/prefs']) == 1
    assert not any(path in {'/ui/setup/prepare', '/ui/setup/projects/default'} for path, _ in setup.posted)
    page.screenshot(path=str(tmp_path / '01-stale-refusal-ignored.png'), full_page=True)
    page.close()

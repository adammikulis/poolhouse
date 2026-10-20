"""Real temporary setup sessions recover through a test-double native holder, without replay."""
import json

import pytest
from test_fleet_bootstrap import browser, device
from test_fleet_initial_setup import initial
from test_startup_model_page import SetupPage

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


def preparing_page(browser, route):
    page = browser.new_page()
    page.set_default_timeout(5000)
    page.route('http://127.0.0.1:8765/**', route)
    page.goto('http://127.0.0.1:8765/')
    page.get_by_role('button', name='Continue', exact=True).click()
    page.locator('#startup-model-choice').wait_for()
    page.get_by_role('button', name='Prepare and continue', exact=True).click()
    return page


def test_old_setup_401_cannot_refuse_or_replay_in_a_new_session(browser, tmp_path):
    setup, held = SetupPage(), []
    def route(request):
        if request.request.url.endswith('/ui/setup/prefs'):
            setup.posted.append(('/ui/setup/prefs', request.request.post_data_json))
            held.append(request)
        else:
            setup.route(request)
    page = preparing_page(browser, route)
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


@pytest.mark.parametrize('outcome', ['renewed', 'cancelled', 'unknown', 'reentry'])
def test_stop_affordance_uses_current_receipt_and_late_status_cannot_restore_it(browser, tmp_path, outcome):
    setup, held_status, held_cancel = SetupPage(state='installing'), [], []
    original = dict(setup.job)
    def route(request):
        path = request.request.url.split('http://127.0.0.1:8765', 1)[-1]
        if path == '/ui/setup/prepare/' + setup.job['id'] and not held_status:
            setup.gets.append(path)
            held_status.append(request)
        elif path.endswith('/cancel'):
            setup.posted.append((path, request.request.post_data_json))
            held_cancel.append(request)
        else:
            setup.route(request)
    page = preparing_page(browser, route)
    stop = page.get_by_role('button', name='Stop preparing', exact=True)
    stop.wait_for()
    assert len(held_status) == 1
    if outcome == 'renewed':
        setup.job['can_cancel'] = False
        page.evaluate('async () => { await fleetModel.signOut(); fleetModel.go("first-run"); }')
        page.get_by_role('heading', name='Preparation in progress', exact=True).wait_for()
    else:
        stop.click()
        assert len(held_cancel) == 1 and stop.is_disabled()
        before = len(setup.gets)
        page.evaluate('() => { void document.querySelector("first-run").poll(); }')
        assert len(setup.gets) == before
        if outcome == 'reentry':
            page.evaluate('() => { fleetModel.go("chat"); fleetModel.go("first-run"); }')
            page.get_by_role('heading', name='Preparing your device', exact=True).wait_for()
        if outcome == 'cancelled':
            setup.job.update(state='cancelled', can_cancel=False, result={}, note='Preparation stopped')
            body, status = setup.job, 200
        else:
            body, status = {'error':'Stop reply was interrupted.'}, 502
        held_cancel[0].fulfill(status=status, body=json.dumps(body), content_type='application/json')
        page.get_by_role('heading', name='Preparation stopped' if outcome == 'cancelled' else 'Preparation in progress', exact=True).wait_for()
    with page.expect_response(lambda response: response.url.endswith('/ui/setup/prepare/' + setup.job['id'])) as reply:
        held_status[0].fulfill(body=json.dumps(original), content_type='application/json')
    reply.value.finished()
    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    assert stop.count() == 0
    assert len([1 for path, _ in setup.posted if path.endswith('/cancel')]) == (0 if outcome == 'renewed' else 1)
    page.get_by_role('button', name='Check status', exact=True).click()
    assert stop.count() == 0
    assert len([1 for path, _ in setup.posted if path.endswith('/cancel')]) == (0 if outcome == 'renewed' else 1)
    page.screenshot(path=str(tmp_path / ('01-stop-' + outcome + '.png')), full_page=True)
    page.close()

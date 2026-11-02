"""An owned native window renews its local session without discarding a draft."""
import pytest
from browser_expect import expect
from test_fleet_bootstrap import browser, device
from test_fleet_initial_setup import initial

__all__ = ['browser', 'device']
pytestmark = pytest.mark.slow


def test_native_session_loss_recovers_once_without_navigation_or_mutation_replay(device, browser):
    assert initial(device)[0] == 200
    device.ui.setup_finished()
    page = browser.new_page()
    calls = []

    def mint(command):
        calls.append(command)
        return device.ui.sessions.mint_ticket('launch-secret')[0]

    page.expose_function('nativeInvoke', mint)
    page.add_init_script('window.__TAURI__={core:{invoke:command=>window.nativeInvoke(command)}}')
    page.goto(f'http://127.0.0.1:{device.port}/ui/#chat')
    page.wait_for_function('() => fleetModel.route === "chat"')
    page.evaluate('''() => {
      window.retainedDocument = document;
      window.retainedInput = document.querySelector('#ask');
      retainedInput.value='Keep this unsaved draft';
      retainedInput.dispatchEvent(new Event('input', {bubbles:true}));
    }''')
    with device.ui.sessions._lock:
        for session in device.ui.sessions._sessions.values():
            session.expires_at = 0
    sent = []
    page.on('request', lambda request: sent.append(request.url) if request.method == 'POST' and request.url.endswith('/ui/chat') else None)
    status = page.evaluate('''async () => {
      const denied=window.previousDenial=await fleetModel.api('/ui/chat', {method:'POST', body:JSON.stringify({model:'unavailable-fixture',text:'Never dispatch this denied request'})}); fleetModel.refused(denied); return denied.status;
    }''')
    assert status == 401
    expect(page.locator('#launch-needed')).to_be_hidden()
    page.wait_for_function('() => fleetModel.route === "chat" && window.retainedDocument === document')
    expect(page.locator('#ask')).to_have_value('Keep this unsaved draft')
    assert calls == ['renew_session']
    assert len(sent) == 1
    page.evaluate('fleetModel.refused(previousDenial)')
    assert page.evaluate('fleetModel.route') == 'chat'
    assert calls == ['renew_session']
    assert page.evaluate('retainedInput === document.querySelector("#ask")')
    assert page.evaluate('async()=> (await fleetModel.api("/ui/session")).signed_in') is True
    page.close()


def test_failed_native_renewal_is_bounded_until_explicit_reconnect(device, browser):
    assert initial(device)[0] == 200
    device.ui.setup_finished()
    page = browser.new_page()
    page.add_init_script('window.nativeCalls=0; window.__TAURI__={core:{invoke:async()=>{nativeCalls++;return null}}}')
    page.goto(f'http://127.0.0.1:{device.port}/ui/#chat')
    page.wait_for_function('() => fleetModel.route === "chat"')
    with device.ui.sessions._lock:
        for session in device.ui.sessions._sessions.values():
            session.expires_at = 0
    page.evaluate('''async()=>{
      const denied=await fleetModel.api('/ui/members');
      fleetModel.refused(denied);fleetModel.refused(denied);
    }''')
    expect(page.locator('#launch-needed-note')).to_contain_text('local connection is unavailable')
    assert page.evaluate('nativeCalls') == 1
    assert 'signed in' not in page.locator('#launch-needed').inner_text()
    assert 'terminal' not in page.locator('#launch-needed').inner_text()
    page.get_by_role('button', name='Reconnect this window').click()
    expect(page.locator('#launch-needed-note')).to_contain_text('local connection is unavailable')
    assert page.evaluate('nativeCalls') == 2
    page.close()


def test_sign_out_during_native_mint_cannot_reopen_session(device, browser):
    assert initial(device)[0] == 200
    device.ui.setup_finished()
    page = browser.new_page()
    page.expose_function('nativeTicket', lambda: device.ui.sessions.mint_ticket('launch-secret')[0])
    page.add_init_script('''window.__TAURI__={core:{invoke:async()=>{
      window.mintPending=true;await new Promise(resolve=>window.releaseMint=resolve);return nativeTicket();
    }}}''')
    page.goto(f'http://127.0.0.1:{device.port}/ui/#chat')
    page.wait_for_function('() => fleetModel.route === "chat"')
    with device.ui.sessions._lock:
        for session in device.ui.sessions._sessions.values():
            session.expires_at = 0
    page.evaluate('''async()=>{fleetModel.refused(await fleetModel.api('/ui/members'))}''')
    page.wait_for_function('() => window.mintPending === true')
    page.evaluate('window.signingOut=fleetModel.signOut(); window.releaseMint()')
    page.evaluate('window.signingOut')
    assert page.evaluate('async()=> (await fleetModel.api("/ui/session")).signed_in') is False
    assert page.evaluate('fleetModel.route') == 'launch-needed'
    page.close()


@pytest.mark.parametrize('phase', ['redemption', 'refresh'])
def test_sign_out_during_renewal_response_cannot_restore_privileged_page(device, browser, phase):
    assert initial(device)[0] == 200
    device.ui.setup_finished()
    page = browser.new_page()
    page.expose_function('nativeTicket', lambda: device.ui.sessions.mint_ticket('launch-secret')[0])
    page.add_init_script('window.__TAURI__={core:{invoke:()=>nativeTicket()}}')
    page.goto(f'http://127.0.0.1:{device.port}/ui/#chat')
    page.wait_for_function('() => fleetModel.route === "chat"')
    with device.ui.sessions._lock:
        for session in device.ui.sessions._sessions.values():
            session.expires_at = 0
    pending = []
    target = '/ui/session' if phase == 'redemption' else '/ui/settings'

    def hold(route):
        if (phase == 'redemption' and route.request.method == 'POST') or phase == 'refresh':
            pending.append(route)
            route.request.frame.evaluate("window.responseHeld=true")
        else:
            route.continue_()

    page.route(f'**{target}', hold)
    with page.expect_request(lambda request: request.url.endswith(target) and
                             (phase == 'refresh' or request.method == 'POST')):
        page.evaluate('''async()=>{fleetModel.refused(await fleetModel.api('/ui/members'))}''')
    page.wait_for_function('() => window.responseHeld === true')
    route = pending[0]
    response = route.fetch()
    page.evaluate('window.signingOut=fleetModel.signOut(); void 0')
    route.fulfill(response=response)
    page.evaluate('window.signingOut')
    assert page.evaluate('async()=> (await fleetModel.api("/ui/session")).signed_in') is False
    assert page.evaluate('fleetModel.route') == 'launch-needed'
    page.close()


def test_stale_native_reopen_probe_cannot_undo_sign_out(device, browser):
    assert initial(device)[0] == 200
    device.ui.setup_finished()
    page = browser.new_page()
    page.add_init_script('window.nativeCalls=0;window.__TAURI__={core:{invoke:async()=>{nativeCalls++;return null}}}')
    page.goto(f'http://127.0.0.1:{device.port}/ui/#chat')
    page.wait_for_function('() => fleetModel.route === "chat"')
    pending = []

    def hold(route):
        if route.request.method == 'GET' and not pending:
            pending.append(route)
            route.request.frame.evaluate('window.reopenHeld=true')
        else:
            route.continue_()

    page.route('**/ui/session', hold)
    page.evaluate('window.dispatchEvent(new Event("poolhouse-native-reopen"))')
    page.wait_for_function('() => window.reopenHeld === true')
    page.evaluate('fleetModel.signOut()')
    pending[0].fulfill(json={'ok': True, 'signed_in': False})
    expect(page.locator('#launch-needed')).to_be_visible()
    assert page.evaluate('nativeCalls') == 0
    assert page.evaluate('async()=> (await fleetModel.api("/ui/session")).signed_in') is False
    page.close()

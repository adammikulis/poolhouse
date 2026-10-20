"""Async UI ownership changes only with the actual local session lifecycle."""
import pytest
from browser_expect import expect
from test_fleet_initial_setup import initial
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401

from poolhouse.fleet import automatic_pools

pytestmark = pytest.mark.slow


@pytest.fixture
def session_page(tmp_path, studio_browser, monkeypatch):  # noqa: F811
    monkeypatch.setattr(automatic_pools, 'offers', lambda port=None: [])
    app = Serving(tmp_path)
    assert initial(app)[0] == 200
    app.ui.setup_finished()
    with studio_browser() as page:
        try:
            ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
            page.goto(f'http://127.0.0.1:{app.port}/ui?launch_ticket={ticket}#pool')
            page.wait_for_function('() => fleetModel.route === "pool"')
            yield app, page
        finally:
            app.close()


def test_session_generation_changes_on_confirmed_redemption_not_get_or_navigation(session_page):
    app, page = session_page
    before = page.evaluate('fleetModel.sessionContext()')
    page.evaluate('async () => { await fleetModel.api("/ui/session"); fleetModel.go("pool"); }')
    assert page.evaluate('fleetModel.sessionContext()') == before
    ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
    calls = []
    page.on('request', lambda request: calls.append(request.url) if request.method == 'POST' and request.url.endswith('/ui/session') else None)
    result = page.evaluate('''async ticket => {
      const options={method:'POST', body:JSON.stringify({ticket})};
      const [first,second]=await Promise.all([fleetModel.api('/ui/session',options),fleetModel.api('/ui/session',options)]);
      return {ok:first.ok, same:first===second};
    }''', ticket)
    assert result == {'ok': True, 'same': True}
    assert len(calls) == 1
    assert page.evaluate('fleetModel.sessionContext()') == before + 1
    page.evaluate('async () => { await fleetModel.api("/ui/session"); fleetModel.go("pool"); }')
    assert page.evaluate('fleetModel.sessionContext()') == before + 1


def test_current_loss_invalidates_once_and_old_denial_cannot_invalidate_replacement(session_page):
    app, page = session_page
    page.route('**/ui/context-denial', lambda route: route.fulfill(status=401, json={'error':'Connection ended'}))
    before = page.evaluate('fleetModel.sessionContext()')
    page.evaluate('''async () => {
      window.oldDenial=await fleetModel.api('/ui/context-denial');
      fleetModel.refused(oldDenial);
    }''')
    assert page.evaluate('fleetModel.sessionContext()') == before + 1
    page.evaluate('async () => fleetModel.refused(await fleetModel.api("/ui/context-denial"))')
    assert page.evaluate('fleetModel.sessionContext()') == before + 1
    ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
    assert page.evaluate("async ticket => (await fleetModel.api('/ui/session',{method:'POST',body:JSON.stringify({ticket})})).ok", ticket)
    after = page.evaluate('fleetModel.sessionContext()')
    page.evaluate('fleetModel.refused(oldDenial)')
    assert page.evaluate('fleetModel.sessionContext()') == after


def test_signout_waits_for_pending_manual_cookie_and_rejects_old_continuation(session_page):
    app, page = session_page
    ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
    held, deletes = [], []
    page.route('**/ui/session', lambda route: held.append(route) if route.request.method == 'POST' else route.continue_())
    page.on('request', lambda request: deletes.append(request.url) if request.method == 'DELETE' and request.url.endswith('/ui/session') else None)
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/ui/session')):
        page.evaluate("ticket => { window.manualAttempt=fleetModel.api('/ui/session',{method:'POST',body:JSON.stringify({ticket})}); }", ticket)
    before = page.evaluate('fleetModel.sessionContext()')
    page.evaluate('() => { window.logoutAttempt=fleetModel.signOut(); }')
    assert page.evaluate('fleetModel.sessionContext()') == before + 1
    assert deletes == []
    held[0].continue_()
    page.evaluate('async () => { await logoutAttempt; }')
    assert page.evaluate('async () => (await manualAttempt).ok') is False
    assert page.evaluate('async () => (await fleetModel.api("/ui/session")).signed_in') is False
    assert len(deletes) == 1


def test_pending_manual_session_prevents_native_overlap(session_page):
    app, page = session_page
    ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
    held = []
    page.route('**/ui/session', lambda route: held.append(route) if route.request.method == 'POST' else route.continue_())
    page.evaluate('() => { window.nativeCalls=0; window.__TAURI__={core:{invoke:async()=>{nativeCalls++;return null}}}; fleetModel.launchOnly=true; }')
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/ui/session')):
        page.evaluate("ticket => { window.manualAttempt=fleetModel.api('/ui/session',{method:'POST',body:JSON.stringify({ticket})}); }", ticket)
    assert page.evaluate('fleetModel.renewNativeSession(true)') is False
    assert page.evaluate('nativeCalls') == 0
    held[0].continue_()
    assert page.evaluate('async () => (await manualAttempt).ok') is True


def test_pending_native_session_refuses_manual_overlap_without_second_redemption(session_page):
    app, page = session_page
    ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
    manual = app.ui.sessions.mint_ticket('launch-secret')[0]
    held = []
    page.expose_function('mintOwnedTicket', lambda _command: ticket)
    page.route('**/ui/session', lambda route: held.append(route) if route.request.method == 'POST' else route.continue_())
    before = page.evaluate('fleetModel.sessionContext()')
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/ui/session')):
        page.evaluate("() => {\n          window.__TAURI__={core:{invoke:command=>window.mintOwnedTicket(command)}};\n          fleetModel.launchOnly=true; fleetModel.go('launch-needed');\n          window.nativeAttempt=fleetModel.renewNativeSession(true);\n        }")
    result = page.evaluate("async ticket => await fleetModel.api('/ui/session',{method:'POST',body:JSON.stringify({ticket})})", manual)
    assert result['status'] == 409 and len(held) == 1
    held[0].continue_()
    assert page.evaluate('async () => await nativeAttempt') is True
    assert page.evaluate('fleetModel.sessionContext()') == before + 1
    expect(page.locator('#app')).to_be_visible()


def test_signout_barrier_blocks_new_establishment_until_owned_delete_finishes(session_page):
    app, page = session_page
    ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
    held, posts = [], []
    page.route('**/ui/session', lambda route: held.append(route) if route.request.method == 'DELETE' else route.continue_())
    page.on('request', lambda request: posts.append(request.url) if request.method == 'POST' and request.url.endswith('/ui/session') else None)
    page.evaluate('() => { window.nativeCalls=0; window.__TAURI__={core:{invoke:async()=>{nativeCalls++;return null}}}; fleetModel.launchOnly=true; }')
    before = page.evaluate('fleetModel.sessionContext()')
    with page.expect_request(lambda request: request.method == 'DELETE' and request.url.endswith('/ui/session')):
        page.evaluate('() => { window.firstSignout=fleetModel.signOut(); window.secondSignout=fleetModel.signOut(); }')
    assert page.evaluate('firstSignout===secondSignout') is True
    assert page.evaluate('fleetModel.sessionContext()') == before + 1
    result = page.evaluate("async ticket => await fleetModel.api('/ui/session',{method:'POST',body:JSON.stringify({ticket})})", ticket)
    assert result['status'] == 409
    assert page.evaluate('fleetModel.renewNativeSession(true)') is False
    assert page.evaluate('fleetModel.reopenNative()') is False
    assert page.evaluate('nativeCalls') == 0 and posts == []
    held[0].continue_()
    page.evaluate('async () => { await firstSignout; }')
    assert page.evaluate('async () => (await fleetModel.api("/ui/session")).signed_in') is False


def test_reopen_probe_does_not_start_renewal_over_new_manual_establishment(session_page):
    app, page = session_page
    ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
    gets, posts = [], []
    page.evaluate('() => { window.nativeCalls=0; window.__TAURI__={core:{invoke:async()=>{nativeCalls++;return null}}}; fleetModel.launchOnly=true; }')
    page.route('**/ui/session', lambda route: gets.append(route) if route.request.method == 'GET' else posts.append(route))
    with page.expect_request(lambda request: request.method == 'GET' and request.url.endswith('/ui/session')):
        page.evaluate('() => { window.reopening=fleetModel.reopenNative(); }')
    with page.expect_request(lambda request: request.method == 'POST' and request.url.endswith('/ui/session')):
        page.evaluate("ticket => { window.manualAttempt=fleetModel.api('/ui/session',{method:'POST',body:JSON.stringify({ticket})}); }", ticket)
    gets[0].fulfill(json={'signed_in': False})
    assert page.evaluate('async () => await reopening') is False
    assert page.evaluate('nativeCalls') == 0
    posts[0].continue_()
    assert page.evaluate('async () => (await manualAttempt).ok') is True


@pytest.mark.parametrize('cancel', [False, True])
def test_new_loss_waits_for_old_native_boot_tail_once_or_stays_signed_out(session_page, cancel):
    app, page = session_page
    page.expose_function('mintOwnedTicket', lambda _command: app.ui.sessions.mint_ticket('launch-secret')[0])
    page.evaluate('''() => {
      window.nativeCalls=0;
      window.__TAURI__={core:{invoke:command=>{
        nativeCalls++; document.body.dataset.nativeMints=String(nativeCalls);
        return mintOwnedTicket(command);
      }}};
      const boot=fleetModel.boot;
      fleetModel.boot=async (...args)=>{
        const result=await boot(...args);
        if(!window.tailHeld){window.tailHeld=true;document.body.dataset.tailHeld="true";await new Promise(resolve=>window.releaseTail=resolve);}
        return result;
      };
      fleetModel.launchOnly=true;fleetModel.go('launch-needed');
      window.firstRenewal=fleetModel.renewNativeSession(true);
    }''')
    expect(page.locator('body')).to_have_attribute('data-tail-held', 'true')
    assert page.evaluate('fleetModel.route') == 'pool'
    with app.ui.sessions._lock:
        for session in app.ui.sessions._sessions.values():
            session.expires_at = 0
    page.evaluate('''async () => {
      window.oldDenial=await fleetModel.api('/ui/members');fleetModel.refused(oldDenial);
      window.firstLoss=fleetModel.renewNativeSession(false);
      fleetModel.refused(await fleetModel.api('/ui/members'));
      window.secondLoss=fleetModel.renewNativeSession(false);
    }''')
    assert page.evaluate('oldDenial.status') == 401
    assert page.evaluate('nativeCalls') == 1
    if cancel:
        page.evaluate('() => { window.signingOut=fleetModel.signOut(); }')
    page.evaluate('releaseTail()')
    if cancel:
        page.evaluate('async () => { await signingOut; }')
        assert page.evaluate('nativeCalls') == 1
        assert page.evaluate('async () => (await fleetModel.api("/ui/session")).signed_in') is False
    else:
        expect(page.locator('body')).to_have_attribute('data-native-mints', '2')
        page.wait_for_function('() => fleetModel.route === "pool"')
        after = page.evaluate('fleetModel.sessionContext()')
        page.evaluate('fleetModel.refused(oldDenial)')
        assert page.evaluate('fleetModel.sessionContext()') == after
        assert page.evaluate('nativeCalls') == 2

"""Live controls through actual authenticated daemon routes and durable finite runs."""
from __future__ import annotations

import json
import re

import pytest
from browser_expect import expect
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401

from poolhouse.fleet import tls
from poolhouse.fleet.discovery import mint_pool
from poolhouse.fleet.membership import Roster

pytestmark = pytest.mark.slow


@pytest.fixture
def live_page(tmp_path, request):
    open_page = request.getfixturevalue('studio_browser')
    app = Serving(tmp_path)
    # Join in-flight browser HTTP handlers before closing their graph connection.
    app.httpd.daemon_threads = False
    app.ui.root = tmp_path / 'traind'
    app.ui.settings.setup_done = True
    identity = tls.identity(tmp_path / 'tls', 'studio')
    app.ui.studio_identity = lambda: identity
    member = mint_pool('live-example-pool', app.keyfile)
    Roster(member.key).enrol(identity.beacon, 'live-device', 'test')
    session = app.ui.sessions.open('owner-session', 'launch-ticket')
    try:
        with open_page() as page:
            page.context.add_cookies([{'name': 'poolhouse_ui', 'value': session.sid,
                                      'url': f'http://127.0.0.1:{app.port}/ui/'}])
            errors, posts = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda req: posts.append((req.url, req.post_data_json)) if req.method == 'POST' and '/ui/studio/' in req.url else None)
            page.goto(f'http://127.0.0.1:{app.port}/ui/#studio')
            page.get_by_label('Search nodes, templates and groups').fill('Record')
            page.locator('.studio-library').get_by_role('button', name=re.compile('^Record')).click()
            page.get_by_label('Workflow title').fill('Live example')
            page.get_by_label('Workflow title').press('Tab')
            expect(page.locator('.studio-status')).to_contain_text('Saved')
            yield page, app, errors, posts
    finally:
        app.close()
        if hasattr(app.ui, 'studio_streams'):
            app.ui.studio_streams.close()
        if hasattr(app.ui, 'studio'):
            app.ui.studio.close()
        if hasattr(app.ui, 'studio'):
            app.ui.studio.store.close()


def open_live(page):
    page.get_by_role('button', name='Live sources', exact=True).click()
    expect(page.get_by_label('Live source', exact=True)).to_be_visible()
    expect(page.get_by_label('Live source', exact=True).locator('option')).not_to_have_count(0)


def create_example(page):
    open_live(page)
    page.get_by_label('Live source', exact=True).select_option(label='Fraud screening · synthetic examples')
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.get_by_role('button', name='Start stream', exact=True)).to_be_enabled()


def test_explicit_sample_start_pause_reload_resume_and_cancel(live_page):
    page, app, errors, posts = live_page
    create_example(page)
    assert not any(url.endswith('/start') for url, _ in posts)
    page.get_by_role('button', name='Preview synthetic sample', exact=True).click()
    expect(page.get_by_label('Synthetic sample preview')).to_be_visible()
    assert isinstance(json.loads(page.get_by_label('Synthetic sample preview').inner_text()), dict)
    assert app.ui.studio_streams.store.active() == []
    page.get_by_role('button', name='Start stream', exact=True).click()
    expect(page.get_by_role('button', name='Pause stream', exact=True)).to_be_enabled()
    page.get_by_role('button', name='Send synthetic sample', exact=True).click()
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 1 · done', timeout=10000)
    expect(page.get_by_label('Live decisions')).to_contain_text('record')
    page.get_by_role('button', name='Pause stream', exact=True).click()
    expect(page.get_by_role('button', name='Resume stream', exact=True)).to_be_enabled()
    page.reload()
    open_live(page)
    expect(page.get_by_role('button', name='Resume stream', exact=True)).to_be_enabled()
    assert len([url for url, _ in posts if url.endswith('/start')]) == 1
    page.get_by_role('button', name='Resume stream', exact=True).click()
    page.get_by_role('button', name='Send synthetic sample', exact=True).click()
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 2 · done', timeout=10000)
    page.get_by_role('button', name='Cancel stream', exact=True).click()
    expect(page.locator('.studio-live')).to_contain_text('cancelled')
    assert not errors


def test_managed_copy_watch_is_explicit_and_records_stay_inert(live_page):
    page, app, errors, _posts = live_page
    path = app.files / 'events.jsonl'
    path.write_text('{"message":"<img src=x onerror=window.injected=true>"}\n')
    # Refresh the authenticated resource inventory as a person returning to Studio does.
    page.reload()
    open_live(page)
    page.locator('.studio-live').get_by_text('Watch a file or folder', exact=True).click()
    page.get_by_label('Uploaded file or folder', exact=True).select_option('events.jsonl')
    page.get_by_role('button', name='Watch uploaded copy', exact=True).click()
    expect(page.locator('.studio-live')).to_contain_text('Watches the uploaded managed copy.')
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    page.get_by_role('button', name='Start stream', exact=True).click()
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 1 · done', timeout=10000)
    expect(page.get_by_label('Live decisions')).to_contain_text('<img src=x')
    assert page.evaluate('window.injected') is None
    assert page.get_by_label('Live decisions').locator('img').count() == 0
    page.get_by_role('button', name='Pause stream', exact=True).click()
    expect(page.get_by_role('button', name='Resume stream', exact=True)).to_be_enabled()
    with path.open('a') as stream:
        stream.write('{"message":"second record"}\n')
    page.get_by_role('button', name='Resume stream', exact=True).click()
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 2 · done', timeout=10000)
    assert not errors


def test_lost_sample_ack_reuses_key_and_attaches_one_event(live_page):
    page, app, errors, posts = live_page
    create_example(page)
    page.get_by_role('button', name='Start stream', exact=True).click()
    expect(page.get_by_role('button', name='Pause stream', exact=True)).to_be_enabled()
    lost = []
    def lose_first_ack(route):
        if route.request.method == 'POST' and not lost:
            lost.append(route.request.post_data_json)
            response = route.fetch()
            assert response.status == 202
            route.abort()
        else:
            route.continue_()
    page.route('**/ui/studio/subscriptions/*/events', lose_first_ack)
    page.get_by_role('button', name='Send synthetic sample', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('fetch', ignore_case=True)
    page.get_by_role('button', name='Send synthetic sample', exact=True).click()
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 1 · done', timeout=10000)
    sent = [body for url, body in posts if url.endswith('/events')]
    assert len(sent) == 2 and sent[0] == sent[1] == lost[0]
    sub = app.ui.studio_streams.store.active()[0]
    assert app.ui.studio_streams.store.page(sub, 0)['events'][0]['sequence'] == 1
    assert len(app.ui.studio_streams.store.page(sub, 0)['events']) == 1
    assert not errors


def test_missing_model_blocks_stream_without_install_or_start(live_page):
    from test_studio_decision_browser import choose_template
    page, app, errors, posts = live_page
    page.get_by_label('Search nodes, templates and groups').fill('')
    choose_template(page, 'Prompt injection check')
    open_live(page)
    page.get_by_label('Live source', exact=True).select_option(label='Prompt injection check · synthetic examples')
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('model', ignore_case=True)
    assert not any(url.endswith('/start') or url.endswith('/decision/install') for url, _ in posts)
    assert app.ui.studio_streams.store.active() == []
    assert not errors


def test_narrow_keyboard_controls_start_only_once(live_page):
    page, _app, errors, posts = live_page
    page.set_viewport_size({'width': 390, 'height': 844})
    toggle = page.get_by_role('button', name='Live sources', exact=True)
    toggle.focus()
    page.keyboard.press('Enter')
    expect(toggle).to_have_attribute('aria-expanded', 'true')
    expect(page.get_by_label('Live source', exact=True)).to_be_focused()
    page.get_by_label('Live source', exact=True).select_option(label='Fraud screening · synthetic examples')
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    start = page.get_by_role('button', name='Start stream', exact=True)
    expect(start).to_be_enabled()
    start.focus()
    page.locator('studio-view').evaluate('editor => editor.liveRenderActions()')
    expect(start).to_be_focused()
    page.keyboard.press('Enter')
    expect(page.get_by_role('button', name='Pause stream', exact=True)).to_be_enabled()
    assert len([url for url, _ in posts if url.endswith('/start')]) == 1
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert not errors


def test_revocation_stops_tail_and_clears_runtime_handles(live_page):
    from poolhouse.fleet import studio_epoch
    page, app, errors, _posts = live_page
    create_example(page)
    page.get_by_role('button', name='Start stream', exact=True).click()
    expect(page.get_by_role('button', name='Pause stream', exact=True)).to_be_enabled()
    page.get_by_role('button', name='Send synthetic sample', exact=True).click()
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 1 · done', timeout=10000)
    authority = app.ui.studio_authority
    studio_epoch.invalidate(authority.root, [authority._context()['pool']], 'invitation-revoked')
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('revoked', timeout=10000)
    expect(page.get_by_label('Live decisions')).to_be_empty()
    expect(page.get_by_role('button', name='Pause stream', exact=True)).to_have_count(0)
    expect(page.get_by_label('Live source', exact=True).locator('option')).to_have_count(0)
    assert page.locator('studio-view').evaluate('editor => editor.liveSub === null')
    assert not errors


def test_new_tail_stays_visible_while_old_event_is_unfinished(live_page):
    """Persist an owned backlog to exercise real pagination without native compute."""
    import hashlib

    from poolhouse.studio.stream_store import sequence_key
    page, app, errors, _posts = live_page
    create_example(page)
    service = app.ui.studio_streams
    sub_id = page.locator('studio-view').evaluate('editor => editor.liveSub.id')
    # Use this isolated daemon's persisted authenticated subscription index.
    sub = next(row for row in service.store.store.graph.query('MATCH (a:StudioSubscriptionIndex) RETURN a.scope AS scope, a.id AS id') if row['id'] == sub_id)
    current = service.store.get('subscription', sub_id, sub['scope'])
    def seed(first, last):
        with service.store.lock, service.store.graph.transaction():
            for sequence in range(first, last + 1):
                ident = hashlib.sha256(f'{sub_id}:{sequence}'.encode()).hexdigest()
                row = {'id': ident, 'scope': current['scope'], 'subscription': sub_id, 'sequence': sequence,
                       'state': 'running' if sequence == 1 else 'done', 'result': {'outputs': []}}
                service.store.store.put('stream-event', row)
                service.store.graph.put_doc(sequence_key(sub_id, sequence), {'id': ident})
            current['next_sequence'] = last + 1
            service.store.put_subscription(current)
    seed(1, 50)
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 1 · running', timeout=10000)
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 50 · done')
    seed(51, 51)
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 51 · done', timeout=10000)
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 1 · running')
    seed(52, 152)
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 152 · done', timeout=10000)
    expect(page.get_by_label('Live decisions')).to_contain_text('outside this live window')
    assert page.locator('.studio-live-event').count() <= 100
    assert not errors


def test_native_bridge_nested_result_selects_original_and_cancel_clears_choice(live_page, tmp_path):
    """Replace only the OS chooser bridge; nonce and source registry stay real."""
    page, app, errors, posts = live_page
    original = tmp_path / 'original.jsonl'
    original.write_text('{"message":"original record"}\n')
    invocations = []
    def native_choose(command, args):
        assert command == 'choose_studio_watch_source'
        assert set(args) == {'requestId', 'nonce'}
        invocations.append(command)
        picks = app.ui.studio_native_picks
        begun = picks.begin(args['requestId'], args['nonce'])
        if len(invocations) == 1:
            return picks.complete(args['requestId'], args['nonce'], begun['claim'], str(original))
        return picks.cancel(args['requestId'], args['nonce'], begun['claim'])
    page.expose_function('__testNativeChoose', native_choose)
    page.add_init_script('window.__TAURI__ = {core:{invoke:(command,args)=>window.__testNativeChoose(command,args)}}')
    page.reload()
    open_live(page)
    page.get_by_label('Live source', exact=True).select_option(label='Fraud screening · synthetic examples')
    page.locator('.studio-live').get_by_text('Watch a file or folder', exact=True).click()
    page.get_by_role('button', name='Choose original file', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Selected original source: original.jsonl')
    selected = page.get_by_label('Live source', exact=True).input_value()
    assert selected
    expect(page.get_by_label('Live source', exact=True).locator('option:checked')).to_contain_text('original.jsonl · original')
    expect(page.locator('.studio-live')).to_contain_text('Watches the original selected file or folder.')
    assert str(original) not in page.locator('.studio-live').inner_text()
    page.get_by_role('button', name='Choose original folder', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('No original source selected')
    expect(page.get_by_label('Live source', exact=True)).to_have_value('')
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Choose an available source')
    assert not any(url.endswith('/subscriptions') for url, _ in posts)
    pick_posts = [body for url, body in posts if url.endswith('/sources/pick')]
    assert len(pick_posts) == 2 and all(set(body) == {'mode', 'request_key'} for body in pick_posts)
    assert not errors

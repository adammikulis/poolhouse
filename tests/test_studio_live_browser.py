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

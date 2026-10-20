"""Built-in synthetic webhook setup through authenticated live daemon routes."""
from __future__ import annotations

import pytest
import test_studio_live_browser
from browser_expect import expect
from test_studio_canvas_browser import studio_browser  # noqa: F401
from test_studio_live_browser import open_live

live_page = test_studio_live_browser.live_page
pytestmark = pytest.mark.slow


@pytest.fixture
def webhook_page(request):
    context = request.getfixturevalue('live_page')
    _page, app, _errors, _posts = context
    app.ui.studio_endpoint = lambda: f'https://127.0.0.1:{app.port}'
    return context


def add_webhook(page, label='Webhook samples'):
    open_live(page)
    page.get_by_text('Add a webhook source', exact=True).click()
    expect(page.get_by_label('Webhook source provider', exact=True)).to_have_value('local-synthetic')
    page.get_by_label('Webhook source name', exact=True).fill(label)
    page.get_by_role('button', name='Add webhook source', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Built-in synthetic webhook source added')


def start_webhook(page):
    add_webhook(page)
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.get_by_role('button', name='Start stream', exact=True)).to_be_enabled()
    page.get_by_role('button', name='Start stream', exact=True).click()
    expect(page.get_by_role('button', name='Send test webhook', exact=True)).to_be_enabled()


def test_create_start_and_registered_webhook_sample_are_explicit(webhook_page):
    page, app, errors, posts = webhook_page
    add_webhook(page)
    expect(page.locator('.studio-live')).to_contain_text('External provider accounts: Not connected')
    assert not any(url.endswith('/start') or url.endswith('/test-webhook') for url, _ in posts)
    configured = next(body for url, body in posts if url.endswith('/sources'))
    assert set(configured) == {'kind', 'provider', 'label', 'idempotency_key'}
    assert configured['provider'] == 'local-synthetic'
    page.get_by_role('button', name='Create paused stream', exact=True).click()
    expect(page.get_by_role('button', name='Send test webhook', exact=True)).to_be_disabled()
    page.get_by_role('button', name='Start stream', exact=True).click()
    page.get_by_role('button', name='Send test webhook', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Test webhook received')
    expect(page.get_by_label('Live decisions')).to_contain_text('Event 1 · done', timeout=10000)
    body = next(body for url, body in posts if url.endswith('/test-webhook'))
    assert set(body) == {'sample_id', 'idempotency_key'}
    assert body['sample_id'] in [item['id'] for item in page.locator('studio-view').evaluate(
        'editor => editor.liveSources.find(source => source.id === editor.liveSub.source_id).samples')]
    sub = app.ui.studio_streams.store.active()[0]
    assert len(app.ui.studio_streams.store.page(sub, 0)['events']) == 1
    assert not errors


def test_lost_test_ack_checks_same_intent_without_second_event(webhook_page):
    page, app, errors, posts = webhook_page
    start_webhook(page)
    lost = []
    def lose_ack(route):
        if not lost:
            lost.append(route.request.post_data_json)
            response = route.fetch()
            assert response.status == 202
            route.abort()
        else:
            route.continue_()
    page.route('**/ui/studio/subscriptions/*/test-webhook', lose_ack)
    page.get_by_role('button', name='Send test webhook', exact=True).click()
    expect(page.get_by_role('button', name='Check test receipt', exact=True)).to_be_enabled()
    expect(page.get_by_label('Registered webhook sample', exact=True)).to_be_disabled()
    page.get_by_role('button', name='Check test receipt', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('Test webhook received')
    submitted = [body for url, body in posts if url.endswith('/test-webhook')]
    assert len(submitted) == 2 and submitted[0] == submitted[1] == lost[0]
    sub = app.ui.studio_streams.store.active()[0]
    assert len(app.ui.studio_streams.store.page(sub, 0)['events']) == 1
    assert not errors


def test_narrow_keyboard_source_name_is_inert_and_never_connects_an_account(webhook_page):
    page, _app, errors, posts = webhook_page
    page.set_viewport_size({'width': 390, 'height': 844})
    open_live(page)
    summary = page.get_by_text('Add a webhook source', exact=True)
    summary.focus()
    summary.press('Enter')
    name = '<img src=x onerror=window.injected=true>'
    page.get_by_label('Webhook source name', exact=True).fill(name)
    create = page.get_by_role('button', name='Add webhook source', exact=True)
    create.focus()
    create.press('Enter')
    expect(page.get_by_label('Live source', exact=True)).to_contain_text(name)
    assert page.evaluate('window.injected') is None
    assert page.locator('.studio-live img').count() == 0
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    assert len([url for url, _ in posts if url.endswith('/sources')]) == 1
    assert not any(url.endswith('/start') or url.endswith('/test-webhook') for url, _ in posts)
    assert not errors


@pytest.mark.parametrize('change', ['new-draft', 'new-workflow'])
def test_delayed_source_create_preserves_newer_configuration(webhook_page, change):
    page, _app, errors, _posts = webhook_page
    open_live(page)
    page.get_by_text('Add a webhook source', exact=True).click()
    previous = page.get_by_label('Live source', exact=True).input_value()
    page.get_by_label('Webhook source name', exact=True).fill('Submitted source')
    held = []
    def delay_ack(route):
        response = route.fetch()
        held.append((route, response))
        page.evaluate('window.webhookAckHeld = true')
    page.route('**/ui/studio/sources', delay_ack)
    page.get_by_role('button', name='Add webhook source', exact=True).click()
    page.wait_for_function('() => window.webhookAckHeld === true')
    if change == 'new-workflow':
        page.get_by_role('button', name='New', exact=True).click()
        expect(page.get_by_label('Workflow title', exact=True)).to_have_value('Untitled experiment')
    page.get_by_label('Webhook source name', exact=True).fill('Newer source draft')
    route, response = held[0]
    route.fulfill(response=response)
    page.wait_for_function('() => !document.querySelector("studio-view").liveBusy')
    expect(page.get_by_label('Webhook source name', exact=True)).to_have_value('Newer source draft')
    expect(page.get_by_label('Live source', exact=True)).to_have_value(previous)
    assert not errors


def test_unresolved_sender_intent_survives_reload_without_retransmission(webhook_page, monkeypatch):
    page, app, errors, posts = webhook_page
    start_webhook(page)
    calls = []
    def unavailable(*_args):
        calls.append(True)
        raise OSError('Controlled unavailable receiver')
    monkeypatch.setattr(app.ui.studio_webhook_setup, '_send', unavailable)
    page.get_by_role('button', name='Send test webhook', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('unresolved outcome')
    expect(page.get_by_role('button', name='Check test receipt', exact=True)).to_be_enabled()
    first = next(body for url, body in posts if url.endswith('/test-webhook'))
    page.reload()
    open_live(page)
    expect(page.get_by_role('button', name='Check test receipt', exact=True)).to_be_enabled()
    expect(page.get_by_label('Registered webhook sample', exact=True)).to_be_disabled()
    page.get_by_role('button', name='Check test receipt', exact=True).click()
    expect(page.locator('.studio-live [role="status"]')).to_contain_text('unresolved outcome')
    submitted = [body for url, body in posts if url.endswith('/test-webhook')]
    assert len(submitted) == 2 and submitted[0] == submitted[1] == first
    assert calls == [True]
    sub = app.ui.studio_streams.store.active()[0]
    assert app.ui.studio_streams.store.page(sub, 0)['events'] == []
    assert not errors


def test_missing_secure_listener_is_unavailable_without_http_fallback(webhook_page):
    page, app, errors, posts = webhook_page
    app.ui.studio_endpoint = lambda: None
    open_live(page)
    page.get_by_text('Add a webhook source', exact=True).click()
    expect(page.get_by_role('button', name='Add webhook source', exact=True)).to_be_disabled()
    expect(page.locator('.studio-live')).to_contain_text('existing secure listener')
    assert not any(url.endswith('/sources') for url, _ in posts)
    assert not errors


def test_delayed_send_error_does_not_overwrite_another_workflow(webhook_page, monkeypatch):
    page, app, errors, _posts = webhook_page
    start_webhook(page)
    def unavailable(*_args):
        raise OSError('Controlled unavailable receiver')
    monkeypatch.setattr(app.ui.studio_webhook_setup, '_send', unavailable)
    armed, held = [], []
    def sender_response(route):
        response = route.fetch()
        assert response.status == 409
        armed.append(True)
        route.fulfill(response=response)
    def hold_following_poll(route):
        if route.request.method == 'GET' and armed and not held:
            response = route.fetch()
            held.append((route, response))
            page.evaluate('window.webhookPollHeld = true')
        else:
            route.continue_()
    page.route('**/ui/studio/subscriptions/*', hold_following_poll)
    page.route('**/ui/studio/subscriptions/*/test-webhook', sender_response)
    page.get_by_role('button', name='Send test webhook', exact=True).click()
    page.wait_for_function('() => window.webhookPollHeld === true')
    page.get_by_role('button', name='New', exact=True).click()
    expect(page.get_by_label('Workflow title', exact=True)).to_have_value('Untitled experiment')
    note = page.locator('.studio-live [role="status"]')
    previous = note.inner_text()
    route, response = held[0]
    route.fulfill(response=response)
    page.wait_for_function('() => !document.querySelector("studio-view").liveBusy')
    expect(note).to_have_text(previous)
    assert not errors

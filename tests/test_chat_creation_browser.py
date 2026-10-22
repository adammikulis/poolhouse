"""Ordinary Chat creates once; uncertain acknowledgement is reconciled without replay."""
import re
import threading

import pytest
from browser_expect import expect
from test_fleet_chat_browser import chat_browser  # noqa: F401

from poolhouse.fleet.conversation_routes import ConversationRoutes

pytestmark = pytest.mark.slow


@pytest.fixture
def creation_page(chat_browser, monkeypatch):  # noqa: F811
    app, page = chat_browser
    app.ui.root = app.ui.conversations.root.parent
    fault = page.creation_fault = CreationFault(monkeypatch)
    created, generation, lookups = [], [], []
    def observe(request):
        path = request.url.split('/ui/', 1)[-1]
        if path == 'conversations' and request.method == 'POST':
            created.append(request.post_data_json)
        if path.startswith('conversations/operations/'):
            lookups.append(path)
    page.on('request', observe)
    def unavailable(route):
        if route.request.method == 'GET':
            route.fallback()
        else:
            generation.append(route.request.post_data_json)
            route.fulfill(status=503, json={'error': 'Fixture generation is unavailable'})
    page.route('**/ui/chat', unavailable)
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    expect(page.locator('#model')).to_have_value('model-a')
    expect(page.locator('#chat-send')).to_be_enabled()
    try:
        yield app, page, created, generation, lookups
    finally:
        fault.release.set()


def send(page, text='Keep this unsent prompt'):
    page.get_by_role('textbox', name='Message', exact=True).fill(text)
    page.get_by_role('button', name='Send', exact=True).click()


def settled(page):
    page.wait_for_function('() => !document.querySelector("chat-view").request')


class CreationFault:
    """Fault only the response after the actual browser proof and store create succeed."""
    def __init__(self, monkeypatch):
        self.mode = None
        self.reached, self.release = threading.Event(), threading.Event()
        original = ConversationRoutes._conversations
        def route(request):
            mode = self.mode
            if request.path == '/ui/conversations' and request.method == 'POST' and mode:
                if mode == 'unknown':
                    request.send(503, {'error': 'Fixture creation acknowledgement unavailable'})
                    return True
                send_response = request.send
                def response(status, value, *extra):
                    if status != 201:
                        return send_response(status, value, *extra)
                    self.reached.set()
                    if mode.startswith('held'):
                        assert self.release.wait(10), 'The owned test did not release its creation response'
                    if mode == 'held-error':
                        return send_response(500, {'error': 'Stale creation error'})
                    if mode == 'lost':
                        return send_response(503, {'error': 'Fixture creation acknowledgement unavailable'})
                    return send_response(status, value, *extra)
                request.send = response
            return original(request)
        monkeypatch.setattr(ConversationRoutes, '_conversations', route)


def uncertain(page, committed):
    page.creation_fault.mode = 'lost' if committed else 'unknown'


def test_actual_create_is_confirmed_before_one_generation(creation_page):
    app, page, created, generation, lookups = creation_page
    send(page)
    expect(page.locator('#chat-note')).to_contain_text('Fixture generation is unavailable')
    assert len(created) == len(generation) == len(app.ui.conversations.all()) == 1
    assert re.fullmatch('[a-f0-9]{32}', created[0]['operation_id'])
    assert generation[0]['conversation'] == app.ui.conversations.all()[0].id
    assert not lookups
    expect(page.locator('#ask')).to_have_value('Keep this unsent prompt')


def test_lost_ack_uses_read_only_receipt_before_explicit_send(creation_page):
    app, page, created, generation, lookups = creation_page
    uncertain(page, True)
    send(page)
    expect(page.locator('#chat-note')).to_contain_text('acknowledgement unavailable')
    assert len(created) == len(app.ui.conversations.all()) == 1 and not generation
    send(page)
    expect(page.locator('#chat-note')).to_contain_text('Fixture generation is unavailable')
    assert len(created) == len(lookups) == len(generation) == 1
    assert lookups == ['conversations/operations/' + created[0]['operation_id']]
    assert len(app.ui.conversations.all()) == 1


def test_unknown_receipt_never_reposts_or_allocates(creation_page):
    app, page, created, generation, lookups = creation_page
    uncertain(page, False)
    send(page)
    expect(page.locator('#chat-note')).to_contain_text('acknowledgement unavailable')
    for _ in range(2):
        send(page)
        settled(page)
    assert len(created) == 1 and len(lookups) == 2
    assert not generation and not app.ui.conversations.all()
    expect(page.locator('#ask')).to_have_value('Keep this unsent prompt')


def test_changed_parameters_refuse_unknown_operation_until_explicit_new(creation_page):
    _app, page, created, generation, lookups = creation_page
    uncertain(page, False)
    send(page)
    expect(page.locator('#chat-note')).to_contain_text('acknowledgement unavailable')
    page.locator('#conversation-details').click()
    page.get_by_label('Temperature', exact=True).fill('0.7')
    page.get_by_label('Temperature', exact=True).press('Tab')
    send(page)
    expect(page.locator('#chat-note')).to_contain_text('earlier conversation setup is unresolved')
    assert len(created) == 1 and not generation and not lookups
    page.locator('#chat-new').click()
    send(page, 'A deliberate separate conversation')
    expect(page.locator('#chat-note')).to_contain_text('acknowledgement unavailable')
    assert len(created) == 2 and created[0]['operation_id'] != created[1]['operation_id']


@pytest.mark.parametrize('change', ['prompt', 'restore', 'session', 'duplicate'])
def test_held_create_never_sends_into_changed_context(creation_page, change):
    app, page, created, generation, _lookups = creation_page
    fault = page.creation_fault
    fault.mode = 'held'
    send(page)
    assert fault.reached.wait(10), 'The actual browser creation did not commit'
    if change == 'prompt':
        page.locator('#ask').fill('A newer unsent prompt')
    elif change == 'restore':
        page.locator('#ask').fill('An intermediate edit')
        page.locator('#ask').fill('Keep this unsent prompt')
    elif change == 'session':
        ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
        assert page.evaluate('ticket => fleetModel.api("/ui/session",{method:"POST",body:JSON.stringify({ticket})})', ticket)['signed_in']
    else:
        page.locator('#ask').press('Enter')
    assert len(created) == 1 and not generation
    fault.release.set()
    settled(page)
    assert len(app.ui.conversations.all()) == 1 and len(created) == 1
    if change == 'duplicate':
        assert len(generation) == 1
    else:
        assert not generation
        expect(page.locator('#ask')).to_have_value('A newer unsent prompt' if change == 'prompt' else 'Keep this unsent prompt')
        assert page.locator('chat-view').evaluate('chat => chat.open') is None


def test_reload_abandons_unsaved_intent_without_recreating(creation_page):
    app, page, created, generation, _lookups = creation_page
    uncertain(page, True)
    send(page)
    expect(page.locator('#chat-note')).to_contain_text('acknowledgement unavailable')
    page.reload()
    expect(page.locator('#ask')).to_have_value('')
    expect(page.locator('#chat-title')).to_have_text('New conversation')
    assert len(created) == len(app.ui.conversations.all()) == 1 and not generation


@pytest.mark.parametrize('change', ['route', 'session'])
def test_late_creation_error_cannot_repaint_or_lock_new_context(creation_page, change):
    app, page, created, generation, _lookups = creation_page
    fault = page.creation_fault
    fault.mode = 'held-error'
    send(page)
    assert fault.reached.wait(10), 'The actual browser creation did not commit'
    if change == 'route':
        page.evaluate('fleetModel.go("pool"); fleetModel.go("chat")')
    else:
        ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
        assert page.evaluate('ticket => fleetModel.api("/ui/session",{method:"POST",body:JSON.stringify({ticket})})', ticket)['signed_in']
    fault.release.set()
    settled(page)
    expect(page.locator('#chat-note')).not_to_contain_text('Stale creation error')
    expect(page.locator('#chat-model-button')).to_be_enabled()
    expect(page.locator('#chat-send')).to_be_visible()
    expect(page.locator('#chat-new')).to_be_enabled()
    assert not generation and len(created) == 1


@pytest.mark.parametrize('change', ['prompt', 'session', 'route'])
def test_changed_intent_during_pending_settings_cannot_create(creation_page, change):
    app, page, created, generation, _lookups = creation_page
    page.locator('chat-view').evaluate("chat => { chat.savePending = new Promise(resolve => window.releaseSettings = resolve); }")
    send(page)
    if change == 'prompt':
        page.locator('#ask').fill('A newer draft while settings finish')
    elif change == 'session':
        ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
        assert page.evaluate('ticket => fleetModel.api("/ui/session",{method:"POST",body:JSON.stringify({ticket})})', ticket)['signed_in']
    else:
        page.evaluate("fleetModel.go('pool')")
    page.evaluate('window.releaseSettings()')
    settled(page)
    assert not created and not generation and not app.ui.conversations.all()
    if change == 'prompt':
        expect(page.locator('#ask')).to_have_value('A newer draft while settings finish')

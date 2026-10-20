"""Chat starts existing inventory models through the existing authenticated route."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from browser_expect import expect
from conftest import write_gguf
from test_fleet_ui import Serving
from test_studio_canvas_browser import studio_browser  # noqa: F401

from poolhouse.fleet import routes
from poolhouse.fleet.conversations import Conversations
from poolhouse.fleet.models import Models
from poolhouse.fleet.serving import Hosting, Served

pytestmark = pytest.mark.slow


@pytest.fixture
def inline_models(tmp_path, monkeypatch, studio_browser):  # noqa: F811
    app = Serving(tmp_path)
    app.ui.settings.setup_done = True
    app.ui.conversations = Conversations(tmp_path / 'chats')
    weights = tmp_path / 'models'
    weights.mkdir()
    model = weights / 'chat-decoder.gguf'
    write_gguf(model, {'general.architecture': 'llama', 'llama.context_length': 16384})
    inventory = [{'path': str(model), 'name': model.name, 'family': 'Fixture', 'servable': True,
                  'id': 'chat-decoder', 'format': 'gguf', 'kind': 'text', 'status': 'installed',
                  'shards': 1, 'is_complete': True, 'files': [],
                  'size_bytes': 0, 'mtime': 1, 'context_length': 16384, 'capabilities': {'chat': True}},
                 {'path': str(weights / 'encoder.gguf'), 'name': 'Encoder', 'family': 'Fixture',
                  'servable': True, 'size_bytes': 0, 'mtime': 1, 'capabilities': {'chat': False}}]
    app.ui.models = Models([weights], weights)
    monkeypatch.setattr(app.ui.models, 'library', lambda: list(inventory))
    current, starts = [], []
    app.ui.serving = SimpleNamespace(live=lambda **_options: list(current))
    monkeypatch.setattr(routes, '_can_serve', lambda: True)

    def start(_host, path, _settings):
        starts.append(str(path))
        served = Served(port=12345, models=[Path(path).name], aliases=[str(path)], capabilities={'chat': True})
        current[:] = [served]
        return served

    monkeypatch.setattr(Hosting, 'start', start)
    with studio_browser() as page:
        try:
            yield app, page, model, starts, current, inventory
        finally:
            app.close()


def open_picker(app, page):
    page.goto(f'http://127.0.0.1:{app.port}/ui#chat')
    page.locator('#chat-model-button').click()
    expect(page.locator('#chat-model-dialog')).to_be_visible()


def test_available_model_starts_in_chat_without_library_navigation_and_preserves_draft(inline_models):
    app, page, model, starts, _current, _inventory = inline_models
    open_picker(app, page)
    expect(page.locator('#chat-model-list button').first).to_have_text('Add New Model (Model Library)')
    expect(page.locator('#chat-model-list')).not_to_contain_text('Encoder')
    page.get_by_role('button', name='Close', exact=True).click()
    expect(page.locator('#ask')).to_be_visible()
    expect(page.locator('#chat-send')).to_be_disabled()
    page.locator('#ask').fill('Keep this unsent draft')
    page.locator('#chat-model-button').click()
    page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True).click()
    expect(page.locator('#chat-model-dialog')).not_to_be_visible()
    expect(page.locator('#model')).to_have_value(model.name)
    expect(page).to_have_url(f'http://127.0.0.1:{app.port}/ui#chat')
    expect(page.locator('#ask')).to_have_value('Keep this unsent draft')
    assert starts == [str(model)]
    page.locator('#chat-model-button').click()
    page.get_by_role('button', name=f'{model.name} Running', exact=True).click()
    assert starts == [str(model)]
    assert app.ui.conversations.all() == []


def test_add_new_model_is_the_only_library_handoff_and_starts_nothing(inline_models):
    app, page, _model, starts, _current, _inventory = inline_models
    open_picker(app, page)
    page.get_by_role('button', name='Add New Model (Model Library)', exact=True).click()
    expect(page).to_have_url(f'http://127.0.0.1:{app.port}/ui#models')
    assert starts == []


def test_lost_start_ack_reconciles_read_only_without_second_start(inline_models):
    app, page, model, starts, _current, _inventory = inline_models

    def lose_ack(route):
        route.fetch()
        route.abort('failed')

    page.route('**/ui/serving', lose_ack)
    open_picker(app, page)
    choice = page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True)
    choice.click()
    expect(page.locator('#chat-note')).to_contain_text('Failed to fetch')
    choice.click()
    expect(page.locator('#chat-model-dialog')).not_to_be_visible()
    expect(page.locator('#model')).to_have_value(model.name)
    assert starts == [str(model)]


@pytest.mark.parametrize('outcome', ['failure', 'wrong-model', 'remote-alias', 'removed'])
def test_failed_or_unconfirmed_start_keeps_chat_model_and_draft(inline_models, monkeypatch, outcome):
    app, page, model, starts, current, inventory = inline_models
    open_picker(app, page)
    page.get_by_role('button', name='Close', exact=True).click()
    page.locator('#ask').fill('Unsent model draft')
    page.locator('#chat-model-button').click()
    if outcome == 'failure':
        def fail(_host, _path, _settings):
            raise ValueError('Model memory is unavailable')
        monkeypatch.setattr(Hosting, 'start', fail)
    elif outcome == 'wrong-model':
        def wrong(_host, path, _settings):
            starts.append(str(path))
            served = Served(port=12345, models=['other.gguf'], aliases=[str(model.parent / 'other.gguf')], capabilities={'chat': True})
            current[:] = [served]
            return served
        monkeypatch.setattr(Hosting, 'start', wrong)
    elif outcome == 'remote-alias':
        page.route('**/ui/chat', lambda route: route.fulfill(json={'models': [
            {'model': model.name, 'path': str(model), 'local': False, 'member': 'remote', 'capabilities': {'chat': True}}]}))
    else:
        inventory.clear()
    page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True).click()
    message = 'Model memory is unavailable' if outcome == 'failure' else 'no longer available' if outcome == 'removed' else 'readiness is not confirmed'
    expect(page.locator('#chat-note')).to_contain_text(message)
    expect(page.locator('#model')).to_have_value('')
    expect(page.locator('#ask')).to_have_value('Unsent model draft')
    expect(page).to_have_url(f'http://127.0.0.1:{app.port}/ui#chat')
    assert starts == ([] if outcome in ('removed', 'failure') else [str(model)])


@pytest.mark.parametrize('action', ['new', 'signout'])
def test_late_start_ack_cannot_select_model_in_new_conversation_or_session(inline_models, action):
    app, page, model, starts, _current, _inventory = inline_models
    held = []
    def hold(route):
        held.append((route, route.fetch()))
        page.locator('body').evaluate("node => node.dataset.startHeld = 'true'")
    page.route('**/ui/serving', hold)
    open_picker(app, page)
    choice = page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True)
    choice.evaluate('node => window.previousModelChoice=node')
    choice.click()
    expect(page.locator('body')).to_have_attribute('data-start-held', 'true')
    page.evaluate('previousModelChoice.click()')
    page.get_by_role('button', name='Close', exact=True).click()
    if action == 'new':
        page.locator('#chat-new').click()
    else:
        page.get_by_role('button', name='Sign out', exact=True).click()
        expect(page.get_by_role('heading', name='Reconnect to Poolhouse', exact=True)).to_be_visible()
    held[0][0].fulfill(response=held[0][1])
    page.wait_for_function('() => !document.querySelector("chat-view").modelPicker.startPending')
    assert page.locator('#model').input_value() == ''
    assert page.locator('chat-view').evaluate('chat => chat.open') is None
    assert starts == [str(model)]


def test_start_then_error_reconciles_without_second_start(inline_models):
    app, page, model, starts, _current, _inventory = inline_models
    def uncertain(route):
        route.fetch()
        route.fulfill(status=500, json={'ok': False, 'error': 'Start acknowledgement unavailable'})
    page.route('**/ui/serving', uncertain)
    open_picker(app, page)
    choice = page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True)
    choice.click()
    expect(page.locator('#chat-note')).to_contain_text('acknowledgement unavailable')
    choice.click()
    expect(page.locator('#chat-model-dialog')).not_to_be_visible()
    expect(page.locator('#model')).to_have_value(model.name)
    assert starts == [str(model)]


def test_accepted_session_replacement_cannot_select_late_started_model(inline_models):
    app, page, model, starts, _current, _inventory = inline_models
    held = []
    def hold(route):
        held.append((route, route.fetch()))
        page.locator('body').evaluate("node => node.dataset.startHeld = 'true'")
    page.route('**/ui/serving', hold)
    open_picker(app, page)
    page.get_by_role('button', name=f'{model.name} Available Start in Chat', exact=True).click()
    expect(page.locator('body')).to_have_attribute('data-start-held', 'true')
    before = page.evaluate('fleetModel.sessionContext()')
    ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
    result = page.evaluate('ticket => fleetModel.api("/ui/session", {method:"POST", body:JSON.stringify({ticket})})', ticket)
    assert result['signed_in'] is True
    assert page.evaluate('fleetModel.sessionContext()') == before + 1
    held[0][0].fulfill(response=held[0][1])
    page.wait_for_function('() => !document.querySelector("chat-view").modelPicker.startPending')
    expect(page.locator('#model')).to_have_value('')
    assert starts == [str(model)]

"""Prepared model identities survive Chat discovery without replacing newer intent."""
from pathlib import Path

import pytest
from browser_expect import expect
from conftest import write_gguf
from test_chat_inline_model_browser import inline_models  # noqa: F401
from test_studio_canvas_browser import studio_browser  # noqa: F401

from poolhouse.fleet.serving import Served

pytestmark = pytest.mark.slow


@pytest.fixture
def handoff_models(inline_models):  # noqa: F811
    app, page, model, starts, current, inventory = inline_models
    resources = [model.parent / folder / 'shared.gguf' for folder in ['one', 'two']]
    for path in resources:
        path.parent.mkdir()
        write_gguf(path, {'general.architecture': 'llama', 'llama.context_length': 16384})
    template = inventory[0]
    inventory[:] = [{**template, 'id': str(index), 'path': str(path), 'name': path.name,
                     'family': f'Fixture {index}'} for index, path in enumerate(resources)]
    current[:] = [Served(port=12345 + index, models=[path.name], aliases=[str(path)], capabilities={'chat': True})
                  for index, path in enumerate(resources)]
    yield app, page, resources, starts, current


def begin(app, page, resource):
    page.goto(f'http://127.0.0.1:{app.port}/ui#pool')
    expect(page.locator('#pool')).to_be_visible()
    page.evaluate('model => {dispatchEvent(new CustomEvent("fleet-chat-ready", {detail:{model}})); fleetModel.go("chat");}', str(resource))


def test_prepared_canonical_path_selects_the_correct_identical_basename(handoff_models):
    app, page, resources, starts, _current = handoff_models
    begin(app, page, resources[1])
    expect(page.locator('#model')).to_have_value(str(resources[1]))
    expect(page.locator('#chat-model-button')).to_have_text('shared.gguf')
    expect(page.locator('#chat-askrow')).to_have_attribute('recipient', 'Message shared.gguf')
    assert not starts


def test_library_open_chat_uses_the_actual_canonical_resource(handoff_models):
    app, page, resources, starts, _current = handoff_models
    page.goto(f'http://127.0.0.1:{app.port}/ui#models')
    page.locator('models-library article[data-model="1"]').get_by_role('button', name='Open chat', exact=True).click()
    expect(page.locator('#model')).to_have_value(str(resources[1]))
    expect(page.locator('#chat-model-button')).to_have_text('shared.gguf')
    assert not starts


@pytest.mark.parametrize('identity', ['missing', 'ambiguous'])
def test_unknown_or_ambiguous_handoff_does_not_choose_first_model(handoff_models, identity):
    app, page, resources, starts, _current = handoff_models
    target = resources[1].parent / 'missing.gguf' if identity == 'missing' else Path('shared.gguf')
    begin(app, page, target)
    expect(page.locator('#chat-note')).to_contain_text('not ready in Chat')
    expect(page.locator('#model')).to_have_value('')
    expect(page.locator('#chat-send')).to_be_disabled()
    assert not starts


@pytest.mark.parametrize('action', ['choice', 'new', 'output', 'session'])
def test_delayed_handoff_cannot_replace_newer_user_context(handoff_models, action):
    app, page, resources, starts, _current = handoff_models
    begin(app, page, resources[0])
    expect(page.locator('#model')).to_have_value(str(resources[0]))
    page.evaluate('fleetModel.go("pool")')
    held = []
    def hold(route):
        held.append((route, route.fetch()))
        page.locator('body').evaluate("node => node.dataset.discoveryHeld = 'true'")
    page.route('**/ui/chat', hold)
    page.evaluate('model => {dispatchEvent(new CustomEvent("fleet-chat-ready", {detail:{model}})); fleetModel.go("chat");}', str(resources[1]))
    expect(page.locator('body')).to_have_attribute('data-discovery-held', 'true')
    if action == 'choice':
        page.locator('#conversation-models button').first.click()
    elif action == 'new':
        page.locator('#chat-new').click()
    elif action == 'output':
        page.locator('#chat-output-tokens').evaluate("node => {node.value=1024; node.dispatchEvent(new Event('input',{bubbles:true}));}")
    else:
        ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
        assert page.evaluate('ticket => fleetModel.api("/ui/session", {method:"POST",body:JSON.stringify({ticket})})', ticket)['signed_in']
    held[0][0].fulfill(response=held[0][1])
    page.wait_for_function('() => !document.querySelector("chat-view").modelPicker.pendingHandoff')
    assert page.locator('#model').input_value() != str(resources[1])
    assert not starts


def test_remote_supplied_path_is_never_a_local_selector(handoff_models):
    app, page, resources, _starts, _current = handoff_models
    begin(app, page, resources[1])
    expect(page.locator('#model')).to_have_value(str(resources[1]))
    assert page.evaluate('path => fleetModel.chatTargetKey({model:"remote-model",path,local:false})', str(resources[1])) == 'remote-model'


def test_new_session_releases_only_the_old_conversation_load(handoff_models):
    app, page, resources, _starts, _current = handoff_models
    begin(app, page, resources[0])
    expect(page.locator('#model')).to_have_value(str(resources[0]))
    conversation = app.ui.conversations.start(model=str(resources[0]))
    held = []
    def hold(route):
        held.append((route, route.fetch()))
        page.locator('body').evaluate("node => node.dataset.conversationHeld = 'true'")
    page.route(f'**/ui/conversations/{conversation.id}', hold)
    page.evaluate('id => void document.querySelector("chat-view").load(id)', conversation.id)
    expect(page.locator('body')).to_have_attribute('data-conversation-held', 'true')
    ticket = app.ui.sessions.mint_ticket('launch-secret')[0]
    assert page.evaluate('ticket => fleetModel.api("/ui/session", {method:"POST",body:JSON.stringify({ticket})})', ticket)['signed_in']
    page.evaluate('() => void document.querySelector("chat-view").draw()')
    expect(page.locator('#chat-model-button')).to_be_enabled()
    held[0][0].fulfill(response=held[0][1])
    expect(page.locator('#chat-tools')).to_be_enabled()
    assert not page.evaluate('document.querySelector("chat-view").loadingConversation')

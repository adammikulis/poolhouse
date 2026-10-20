"""One conversation retains its model while tool capabilities change."""
import re

import pytest
from browser_expect import expect
from test_chat_workspace_discovery_browser import activity_workspace, chat_browser, team
from test_coding_browser import coding_browser

__all__ = ['activity_workspace', 'chat_browser', 'coding_browser', 'team']
pytest_plugins = ['node_kit']
pytestmark = pytest.mark.slow


def test_tools_are_explicit_and_do_not_replay_or_switch_conversation(coding_browser):
    served, page, project = coding_browser
    saved = served.ui.conversations.start(model='test-model', title='One conversation')
    served.ui.conversations.append(saved.id, 'user', 'Prior direct question')
    served.ui.conversations.append(saved.id, 'assistant', 'Prior direct response')
    starts = []
    page.on('request', lambda request: starts.append(request.url) if request.method == 'POST' and '/start' in request.url else None)
    page.goto(f'http://127.0.0.1:{served.port}/ui#chat')
    page.get_by_role('link', name='One conversation', exact=True).click()
    expect(page.get_by_label('Tools', exact=True)).not_to_be_checked()
    assert page.get_by_label('Conversation mode', exact=True).count() == 0
    page.get_by_label('Tools', exact=True).check()
    expect(page.get_by_label('Tool runtime', exact=True)).to_have_value('')
    expect(page.get_by_label('Tool permissions', exact=True)).to_have_value('')
    expect(page.locator('chat-view #model')).to_have_value('test-model')
    expect(page.locator('#chat-send')).to_be_disabled()
    page.get_by_label('Tool runtime', exact=True).select_option('codex')
    page.get_by_label('Tool permissions', exact=True).select_option('read-only')
    page.get_by_label('Project directory', exact=True).fill(str(project))
    page.get_by_label('Project directory', exact=True).press('Tab')
    assert starts == []
    assert len(served.ui.conversations.get(saved.id).messages) == 2
    composer = page.get_by_role('textbox', name='Message', exact=True)
    composer.fill('A single new tool request')
    composer.press('Enter')
    expect(page.locator('#chat-messages .message-body').last).to_contain_text(re.compile(r'New request:\s*A single new tool request'))
    expect(page.get_by_role('button', name='Stop', exact=True)).to_be_hidden()
    page.get_by_label('Tools', exact=True).uncheck()
    page.locator('chat-view').evaluate('async chat => { await chat.savePending; }')
    assert page.locator('chat-view').evaluate('chat => chat.open') == saved.id
    expect(page.locator('chat-view #model')).to_have_value('test-model')
    current = served.ui.conversations.get(saved.id)
    assert current.settings['harness'] == '' and 'mode' not in current.settings
    assert len(current.messages) == 4 and len(starts) == 1


def test_unified_picker_agent_is_a_scoped_dm_not_a_model_or_tool_choice(activity_workspace):
    app, page, _identifier, member = activity_workspace
    sent = []
    page.on('request', lambda request: sent.append(request.url) if request.method == 'POST' and '/board/post' in request.url else None)
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    expect(page.locator('#conversation-directory')).to_contain_text(member.name)
    before = page.locator('chat-view').evaluate('chat => ({model:chat.picker.value, settings:chat.settings(), cid:chat.open})')
    page.locator('#chat-model-button').click()
    search = page.get_by_label('Search models and workspace agents', exact=True)
    search.fill(member.name)
    choice = page.locator(f'[data-picker-kind="agent"][data-agent-id="{member.name}"]')
    expect(choice).to_contain_text('Agent · scoped direct message')
    choice.focus()
    choice.press('Enter')
    board = page.locator('chat-view ml-board')
    expect(board.get_by_role('textbox', name='Message', exact=True)).to_be_visible()
    assert board.evaluate('board => board.target().to') == member.name
    assert page.locator('chat-view').evaluate('chat => ({model:chat.picker.value, settings:chat.settings(), cid:chat.open})') == before
    assert sent == []


def test_absent_global_workspace_selection_uses_current_preferred_scope(activity_workspace):
    app, page, identifier, _member = activity_workspace
    page.add_init_script("localStorage.setItem('poolhouse.conversations.project','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')")
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    expect(page.get_by_label('Workspace', exact=True)).to_have_value(identifier)
    expect(page.locator('#conversation-workspace-name')).to_have_text('active-workspace')
    assert page.locator('chat-view').evaluate('chat => chat.open') is None


@pytest.mark.parametrize('switch_conversation', [True, False])
def test_old_tool_catalogue_cannot_overwrite_newer_capability_controls(coding_browser, switch_conversation):
    served, page, _project = coding_browser
    first = served.ui.conversations.start(model='test-model', title='First tool context')
    second = served.ui.conversations.start(model='test-model', title='Second tool context')
    page.unroute('**/ui/coding/catalogue')
    held = []
    page.route('**/ui/coding/catalogue', lambda route: held.append(route))
    page.goto(f'http://127.0.0.1:{served.port}/ui#chat')
    page.get_by_role('link', name=first.title, exact=True).click()
    with page.expect_request('**/ui/coding/catalogue'):
        page.get_by_label('Tools', exact=True).check()
    page.wait_for_function('() => document.querySelector("chat-view").coding.tools.checked')
    if switch_conversation:
        page.get_by_role('link', name=second.title, exact=True).click()
        expect(page.locator('#chat-title')).to_have_text(second.title)
    else:
        page.get_by_label('Tools', exact=True).uncheck()
    page.locator('chat-view').evaluate('chat => { chat.coding.harness.append(new Option("Current runtime", "current")); chat.coding.harness.value="current"; chat.coding.role.append(new Option("Current permissions", "current-role")); chat.coding.role.value="current-role"; }')
    assert len(held) == 1
    held[0].fulfill(json={'ok': True, 'harnesses': [{'name':'codex', 'available':True}], 'roles':[{'name':'read-only', 'summary':'Read only'}]})
    page.locator('chat-view').evaluate('async chat => { await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame); }')
    expect(page.get_by_label('Tools', exact=True)).not_to_be_checked()
    assert page.locator('#chat-harness').input_value() == 'current'
    assert page.locator('#chat-role').input_value() == 'current-role'
    assert page.locator('chat-view').evaluate('chat => chat.coding.data || null') is None
    assert served.ui.conversations.get(first.id).messages == []

"""Current authorized agents remain visible before the first direct message."""
import pytest
from browser_expect import expect
from test_chat_workspace_discovery_browser import activity_workspace, chat_browser, team

__all__ = ['activity_workspace', 'chat_browser', 'team']
pytest_plugins = ['node_kit']
pytestmark = pytest.mark.slow


def test_current_agents_are_visible_with_no_direct_history_and_open_only_a_draft(activity_workspace):
    app, page, _identifier, member = activity_workspace
    sent = []
    page.on('request', lambda request: sent.append(request.url) if request.method == 'POST' and '/board/post' in request.url else None)
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    expect(page.locator('#conversation-workspace-name')).to_have_text('active-workspace')
    directory = page.locator('#conversation-directory')
    agent = directory.locator(f'button[title="{member.name}"]')
    expect(agent).to_be_visible()
    expect(agent).to_contain_text('Agent')
    expect(agent).to_have_attribute('aria-description', f'Agent identity {member.name}')
    expect(page.locator('#conversation-direct')).to_contain_text('No direct messages yet.')
    agent.focus()
    page.locator('chat-view ml-board').evaluate('board => board.load()')
    expect(agent).to_be_focused()
    agent.press('Enter')
    board = page.locator('chat-view ml-board')
    assert board.evaluate('board => board.target().to') == member.name
    expect(page.locator('#conversation-team-heading')).to_contain_text('active-workspace')
    expect(page.locator('#conversation-team-heading')).to_contain_text(member.name)
    expect(board.get_by_role('textbox', name='Message', exact=True)).to_be_visible()
    assert sent == []


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('width', [1440, 390])
def test_agent_directory_search_scope_and_keyboard_use_theme_tokens(activity_workspace, tmp_path, theme, width):
    from poolhouse.fleet.themes import PRESETS

    app, page, _identifier, member = activity_workspace
    page.set_viewport_size({'width': width, 'height': 900})
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    page.wait_for_function('() => !!fleetModel.preferences.resolved_theme')
    page.evaluate('theme => fleetModel.setPreferences({...fleetModel.preferences, resolved_theme:theme})', PRESETS[theme])
    if width == 390:
        page.get_by_role('button', name='Channels and model chats').click()
    search = page.get_by_label('Search conversations and channels', exact=True)
    search.fill('no-agent-matches-this')
    expect(page.locator('#conversation-directory')).to_contain_text('No agents match your search.')
    search.fill(member.name)
    agent = page.locator(f'#conversation-directory button[title="{member.name}"]')
    expect(agent).to_have_count(1)
    agent.focus()
    page.locator('chat-view ml-board').evaluate('board => board.load()')
    expect(agent).to_be_focused()
    agent.press('Enter')
    expect(page.locator('#conversation-team-heading')).to_contain_text('Shared workspace')
    composer = page.locator('chat-view ml-board').get_by_role('textbox', name='Message', exact=True)
    expect(composer).to_be_in_viewport()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(tmp_path / f'chat-directory-{theme}-{width}.png'), full_page=True)


def test_stale_agent_button_cannot_open_dm_after_workspace_switch(activity_workspace):
    app, page, _identifier, member = activity_workspace
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    button = page.locator(f'#conversation-directory button[title="{member.name}"]')
    expect(button).to_be_visible()
    button.evaluate('button => window.previousAgentButton=button')
    page.get_by_label('Workspace', exact=True).select_option('local')
    expect(page.locator('#conversation-workspace-name')).to_have_text('Device-local board')
    expect(page.locator('#conversation-workspace-status')).to_have_text('Only on this device')
    page.evaluate('previousAgentButton.click()')
    assert page.evaluate('fleetModel.route') == 'chat'
    assert page.locator('chat-view ml-board').get_attribute('endpoint') == '/ui/board'


def test_person_required_is_distinct_from_connection_failure_without_enrollment(activity_workspace):
    app, page, identifier, _member = activity_workspace
    enrolled = []
    page.on('request', lambda request: enrolled.append(request.url) if request.method == 'POST' and '/board/' in request.url else None)
    page.route(f'**/ui/projects/{identifier}/board/boards', lambda route: route.fulfill(
        status=403, json={'error': 'Workspace access requires a person session.', 'person_setup_required': True}))
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    expect(page.locator('#conversation-workspace-status')).to_have_text('Workspace access needed')
    expect(page.locator('#conversation-channels').get_by_role('button', name='Join workspace as person')).to_be_visible()
    expect(page.locator('#conversation-directory button')).to_have_count(0)
    assert enrolled == []

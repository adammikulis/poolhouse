"""Current workspace activity stays discoverable across asynchronous Chat lifecycles."""
import re

import pytest
import test_conversations_workspace as team_fixtures
import test_fleet_chat_browser as chat_fixtures
from browser_expect import expect
from person_kit import member_on

from poolhouse.fleet import project_client
from poolhouse.fleet.projects import ProjectRegistry, identity
from poolhouse.net import git

chat_browser = chat_fixtures.chat_browser
team = team_fixtures.team
pytest_plugins = ['node_kit']
pytestmark = pytest.mark.slow


@pytest.fixture
def activity_workspace(chat_browser, team, tmp_path, monkeypatch):
    monkeypatch.delenv('POOLHOUSE_BOARD', raising=False)
    checkout = tmp_path / 'active-workspace'
    checkout.mkdir()
    git.run(['init', '-q'], cwd=checkout)
    git.run(['remote', 'add', 'origin', 'https://code.example.invalid/team/current.git'], cwd=checkout)
    member = member_on(team, checkout, 'activity-helper')
    member.post('#general', 'note', 'Current authorized activity')
    member.room('create', '#experiments')
    registry = ProjectRegistry(tmp_path / 'registry', 'fixture-device', (checkout,))
    monkeypatch.setattr(project_client, 'members', lambda ui: [])
    app, page = chat_browser
    app.ui.projects = registry
    session = app.ui.sessions.open('fixture-person', 'launch-ticket')
    page.context.add_cookies([{'name': 'poolhouse_ui', 'value': session.sid,
                              'url': f'http://127.0.0.1:{app.port}/ui', 'httpOnly': True}])
    return app, page, identity(checkout), member


def test_chat_sidebar_loads_actual_activity_and_keeps_discovery_active(activity_workspace):
    app, page, identifier, _member = activity_workspace
    page.add_init_script('''customElements.whenDefined('poolhouse-board').then(() => {
      const prototype=customElements.get('poolhouse-board').prototype, original=prototype.setActive;
      prototype.setActive=function(active){(window.boardActivityCalls ||= []).push({active,boards:this.boards?.length,route:window.fleetModel?.route});return original.call(this,active);};
    });''')
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    expect(page.get_by_label('Workspace', exact=True)).to_have_value(identifier)
    expect(page.locator('#conversation-channels').get_by_role('button', name='#experiments', exact=True)).to_be_visible()
    board = page.locator('chat-view poolhouse-board')
    assert board.evaluate('board => board.stopped') is False, page.evaluate('window.boardActivityCalls')
    page.locator('#conversation-channels').get_by_role('button', name=re.compile(r'^#general(?: · \d+)?$')).click()
    focus = board.get_by_role('region', name='Message focus')
    expect(focus.get_by_role('checkbox', name='Hide agent chatter')).to_be_checked()
    expect(focus.get_by_role('status')).to_have_text('1 agent message hidden on this loaded page.')
    expect(board.locator('.msg pre')).to_have_count(0)
    focus.get_by_role('button', name='Show all messages', exact=True).click()
    expect(board.locator('.msg pre')).to_contain_text('Current authorized activity')
    page.evaluate('fleetModel.go("chat")')
    assert board.evaluate('board => board.stopped') is False
    page.evaluate('fleetModel.go("settings")')
    assert board.evaluate('board => board.stopped') is True


@pytest.mark.parametrize('saved', ['', 'a' * 32])
def test_unavailable_workspace_does_not_hide_current_pool_or_silently_switch(activity_workspace, saved):
    app, page, identifier, _member = activity_workspace
    original = app.ui.projects.boards
    app.ui.projects.boards = lambda: [{'id': 'a' * 32, 'name': 'Unavailable checkout',
                                           'available': False, 'preferred': False}, *original()]
    if saved:
        page.add_init_script(f'localStorage.setItem("poolhouse.conversations.project", "{saved}")')
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    selector = page.get_by_label('Workspace', exact=True)
    expect(selector).to_have_value(saved or identifier)
    if saved:
        expect(page.locator('#conversation-channels')).to_contain_text('workspace is unavailable')
        assert page.locator('chat-view poolhouse-board').count() == 0
        selector.select_option(identifier)
    expect(page.locator('#conversation-channels').get_by_role('button', name='#experiments', exact=True)).to_be_visible()


def test_removed_board_navigation_cannot_replace_current_sidebar(activity_workspace):
    app, page, identifier, _member = activity_workspace
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    expect(page.locator('#conversation-channels').get_by_role('button', name='#experiments', exact=True)).to_be_visible()
    page.locator('chat-view poolhouse-board').evaluate('board => window.previousBoard=board')
    page.get_by_label('Workspace', exact=True).select_option('local')
    page.get_by_label('Workspace', exact=True).select_option(identifier)
    expect(page.locator('#conversation-channels').get_by_role('button', name='#experiments', exact=True)).to_be_visible()
    page.evaluate('previousBoard.dispatchEvent(new CustomEvent("board-navigation", {detail:{error:"Stale failure"}}))')
    expect(page.locator('#conversation-channels')).not_to_contain_text('Stale failure')
    assert page.locator('chat-view poolhouse-board').evaluate('board => board.stopped') is False


def test_narrow_keyboard_workspace_selection_reads_current_activity(activity_workspace):
    app, page, identifier, _member = activity_workspace
    page.set_viewport_size({'width': 390, 'height': 844})
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    page.get_by_role('button', name='Channels and model chats').click()
    selector = page.get_by_label('Workspace', exact=True)
    expect(selector).to_have_value(identifier)
    selector.focus()
    selector.press('ArrowDown')
    selector.press('ArrowUp')
    expect(selector).to_have_value(identifier)
    expect(page.locator('#conversation-channels').get_by_role('button', name='#experiments', exact=True)).to_be_attached()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


@pytest.mark.parametrize('switch', [False, True])
def test_current_denial_clears_handles_but_removed_board_cannot_clear_new_selection(activity_workspace, switch):
    app, page, identifier, _member = activity_workspace
    page.goto(f'http://127.0.0.1:{app.port}/ui/#chat')
    expect(page.locator('#conversation-channels').get_by_role('button', name='#experiments', exact=True)).to_be_visible()
    pending = []
    page.route(f'**/ui/projects/{identifier}/board/boards', lambda route: pending.append(route))
    with page.expect_request(lambda request: request.url.endswith('/board/boards')):
        page.locator('chat-view poolhouse-board').evaluate('board => { window.deniedLoad = board.load(); }')
    page.evaluate('true')
    if switch:
        page.get_by_label('Workspace', exact=True).select_option('local')
    pending[0].fulfill(status=403, json={'error': 'Workspace access denied.'})
    page.evaluate('window.deniedLoad')
    if switch:
        assert page.locator('chat-view poolhouse-board').get_attribute('endpoint') == '/ui/board'
        expect(page.locator('#conversation-channels')).not_to_contain_text('Workspace access denied.')
    else:
        expect(page.locator('#conversation-channels')).to_contain_text('Workspace access denied.')
        board = page.locator('chat-view poolhouse-board')
        assert board.evaluate('board => [board.me, board.boards.length, board.dms.length, board.items.length, board.target()]') == ['', 0, 0, 0, None]
        expect(board.get_by_role('textbox', name='Message', exact=True)).to_have_count(0)

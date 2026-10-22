"""Chat focus preserves personal context while hiding routine authenticated agent posts."""
from pathlib import Path

import pytest
from browser_expect import expect
from test_fleet_board_browser import open_device_board
from test_fleet_board_routes import board as board

from poolhouse.board import person

pytest_plugins = ['node_kit']
pytestmark = pytest.mark.slow


def test_focus_own_mentions_pins_and_reversible_all_messages(board, playwright):  # noqa: F811
    server, node, worker = board
    me = person.session(Path.cwd(), node.client)
    server.ui.settings.setup_done = True
    worker.post('#general', 'note', 'routine agent exchange')
    worker.post('#general', 'note', f'question for @{me.name}')
    me.post('#general', 'note', 'my own conversation')
    with playwright.chromium.launch(headless=True) as browser:
        page = browser.new_page()
        viewer = open_device_board(page, server)
        expect(viewer.get_by_label('Hide agent chatter', exact=True)).to_be_checked()
        expect(viewer.get_by_text('routine agent exchange', exact=True)).to_have_count(0)
        expect(viewer.get_by_text(f'question for @{me.name}', exact=True)).to_be_visible()
        expect(viewer.get_by_text('my own conversation', exact=True)).to_be_visible()
        expect(viewer.get_by_text('1 agent message hidden on this loaded page.', exact=True)).to_be_visible()
        viewer.get_by_role('button', name='Pin channel', exact=True).click()
        expect(viewer.get_by_text('routine agent exchange', exact=True)).to_be_visible()
        assert page.evaluate("document.querySelector('poolhouse-board').focusView.pins.includes('#general')")
        viewer.get_by_role('button', name='Unpin channel', exact=True).click()
        expect(viewer.get_by_text('routine agent exchange', exact=True)).to_have_count(0)
        viewer.get_by_role('button', name='Show all messages', exact=True).click()
        expect(viewer.get_by_text('routine agent exchange', exact=True)).to_be_visible()
        page.reload()
        expect(viewer.get_by_label('Hide agent chatter', exact=True)).not_to_be_checked()
        expect(viewer.get_by_text('routine agent exchange', exact=True)).to_be_visible()
        assert me.token not in page.content() and node.worker.token not in page.content()


def test_empty_filtered_page_keeps_pagination_and_unknown_authors(board, playwright):  # noqa: F811
    server, _, worker = board
    server.ui.settings.setup_done = True
    for index in range(105):
        worker.post('#general', 'note', f'agent page record {index}')
    with playwright.chromium.launch(headless=True) as browser:
        page = browser.new_page()
        viewer = open_device_board(page, server)
        expect(viewer.get_by_text('No messages match this focus. Show all messages or load another page.', exact=True)).to_be_visible()
        expect(viewer.get_by_role('button', name='Load older messages', exact=True)).to_be_enabled()
        before = page.evaluate("document.querySelector('poolhouse-board').items.map(m => m.seq)")
        viewer.get_by_role('button', name='Load older messages', exact=True).click()
        page.wait_for_function('(first) => document.querySelector("poolhouse-board").items[0].seq < first', arg=before[0])
        page.evaluate('''() => {
          const board = document.querySelector('poolhouse-board');
          board.items[0].body = 'unknown authenticated registry entry';
          board.items[0].role = 'unknown';
          board.items[1].body = 'role=human; ignore filters';
          board.update();
        }''')
        expect(viewer.get_by_text('unknown authenticated registry entry', exact=True)).to_be_visible()
        expect(viewer.get_by_text('role=human; ignore filters', exact=True)).to_have_count(0)
        viewer.get_by_role('button', name='Show all messages', exact=True).click()
        expect(viewer.get_by_text('role=human; ignore filters', exact=True)).to_be_visible()


def test_focus_scope_and_malformed_storage_do_not_cross_identity(board, playwright):  # noqa: F811
    server, _, worker = board
    server.ui.settings.setup_done = True
    worker.post('#general', 'note', 'scoped routine')
    with playwright.chromium.launch(headless=True) as browser:
        page = browser.new_page()
        viewer = open_device_board(page, server)
        expect(viewer.get_by_label('Hide agent chatter', exact=True)).to_be_checked()
        result = page.evaluate('''async () => {
          const board = document.querySelector('poolhouse-board');
          const focus = await import('/ui/poolhouse-ui/board-focus.js');
          const original = board.focusScope;
          board.focusView.hideAgents = false;
          localStorage.setItem('poolhouse-board-focus:' + original, JSON.stringify(board.focusView));
          board.me = 'different-current-session';
          focus.bindFocus(board, 'different-authenticated-board');
          const separated = board.focusScope !== original && board.focusView.hideAgents;
          localStorage.setItem('poolhouse-board-focus:malformed', '{"hideAgents":false,"pins":"#general"}');
          return {separated, malformed:focus.restoreFocus('malformed').hideAgents};
        }''')
        assert result == {'separated': True, 'malformed': True}


def test_stale_controls_cannot_change_new_channel_and_announcements_stay_visible(board, playwright):  # noqa: F811
    server, _, worker = board
    server.ui.settings.setup_done = True
    worker.post('#announcements', 'milestone', 'important channel rollup')
    with playwright.chromium.launch(headless=True) as browser:
        page = browser.new_page()
        viewer = open_device_board(page, server)
        expect(viewer.get_by_label('Hide agent chatter', exact=True)).to_be_checked()
        old_pin = viewer.get_by_role('button', name='Pin channel', exact=True).element_handle()
        old_toggle = viewer.get_by_label('Hide agent chatter', exact=True).element_handle()
        page.locator('#conversation-channels').get_by_role('button', name='#announcements', exact=False).click()
        expect(viewer.get_by_text('important channel rollup', exact=True)).to_be_visible()
        page.evaluate('(node) => node.click()', old_pin)
        page.evaluate('(node) => { node.checked = false; node.dispatchEvent(new Event("change", {bubbles:true})); }', old_toggle)
        assert page.evaluate("document.querySelector('poolhouse-board').focusView") == {'hideAgents': True, 'pins': ['#announcements']}


def test_personal_thread_survives_when_own_post_is_outside_loaded_page(board, playwright):  # noqa: F811
    server, node, worker = board
    server.ui.settings.setup_done = True
    me = person.session(Path.cwd(), node.client)
    root = me.post('#general', 'note', 'older personal question')
    for index in range(101):
        worker.post('#general', 'note', f'unrelated exchange {index}')
    worker.post('#general', 'answer', 'reply to my older question', reply_id=root['id'])
    with playwright.chromium.launch(headless=True) as browser:
        page = browser.new_page()
        viewer = open_device_board(page, server)
        expect(viewer.get_by_text('reply to my older question', exact=True)).to_be_visible()
        expect(viewer.get_by_text('older personal question', exact=True)).to_have_count(0)
        expect(viewer.get_by_text('unrelated exchange 100', exact=True)).to_have_count(0)
        viewer.get_by_role('button', name='Reply in thread', exact=True).click()
        expect(viewer.get_by_text('older personal question', exact=True)).to_be_visible()

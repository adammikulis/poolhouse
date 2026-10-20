"""The integrated Board participates as its person owner in an isolated workspace."""

from pathlib import Path

import pytest
from test_fleet_board_routes import board as board

from poolhouse.board import person

pytest_plugins = ["node_kit"]
pytestmark = pytest.mark.slow


def open_device_board(page, server):
    """The conversations screen on the device-local workspace, and the board it hosts."""
    page.goto(f'http://127.0.0.1:{server.port}/ui/#board')
    page.get_by_label('Workspace', exact=True).select_option('local')
    return page.locator('chat-view ml-board')


def open_direct(page, agent):
    """Start (or reopen) a direct conversation from the sidebar's agent directory."""
    page.locator('#conversation-directory').get_by_title(agent, exact=True).click()


def test_person_channel_thread_dm_drafts_and_live_reply(board, playwright):  # noqa: F811
    from playwright.sync_api import expect

    server, node, worker = board
    me = person.session(Path.cwd(), node.client)
    server.ui.settings.setup_done = True
    worker.post('#general', 'note', '<img src=x> Native sensors are ready.')
    with playwright.chromium.launch(headless=True) as browser:
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        viewer = open_device_board(page, server)
        viewer.get_by_role('button', name='Show all messages', exact=True).click()
        expect(viewer.get_by_text('<img src=x> Native sensors are ready.', exact=True)).to_be_visible()
        assert viewer.locator('img').count() == 0
        viewer.get_by_role('button', name='Reply in thread', exact=True).click()
        viewer.locator('textarea').fill('Please check the stop sign.')
        viewer.get_by_role('button', name='Send', exact=True).click()
        expect(viewer.get_by_text('Please check the stop sign.', exact=True)).to_be_visible()
        rows = worker.read(channel='#general', kind='message').entries
        assert rows[-1].sender == me.name and rows[-1].fields['thread_id'] == rows[0].id
        viewer.get_by_role('button', name='Back to #general', exact=True).click()
        viewer.locator('textarea').fill('Unsent channel draft')
        open_direct(page, node.worker.name)
        expect(viewer.locator('textarea')).to_have_value('')
        viewer.locator('textarea').fill('Inspect the camera frames.')
        viewer.locator('textarea').press('Control+Enter')
        expect(viewer.get_by_text('Inspect the camera frames.', exact=True)).to_be_visible()
        assert worker.read(inbox=True).entries[-1].sender == me.name
        worker.post(me.name, 'note', 'Camera frames checked.')
        expect(viewer.get_by_text('Camera frames checked.', exact=True)).to_be_visible(timeout=10000)
        viewer.get_by_role('button', name='Mark read', exact=True).click()
        page.wait_for_function("() => document.querySelector('ml-board').dms.every(dm => !dm.unread)")
        page.locator('#conversation-channels').get_by_role('button', name='#general', exact=False).click()
        expect(viewer.locator('textarea')).to_have_value('Unsent channel draft')
        page.reload()
        expect(page.get_by_label('Workspace', exact=True)).to_have_value('local')
        expect(viewer.locator('textarea')).to_have_value('Unsent channel draft')
        assert me.token not in page.content() and node.worker.token not in page.content()
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path='/private/tmp/poolhouse-board-audit-mobile.png', full_page=True)
        page.evaluate("window.fleetModel.go('training')")
        expect(viewer).to_be_hidden()
        assert not errors



"""Shared workspace failures remain scoped and readable in narrow Conversations."""

import pytest
from browser_expect import expect
from test_fleet_ui import Serving

pytestmark = pytest.mark.slow


def test_mobile_shared_workspace_failure_never_falls_back_and_escapes_labels(tmp_path, playwright):
    server = Serving(tmp_path)
    server.ui.settings.setup_done = True
    project = 'a' * 32
    requested = []
    try:
        with playwright.chromium.launch(headless=True) as browser:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.route('**/ui/projects', lambda route: route.fulfill(json={'ok': True, 'workspaces': [
                {'id': project, 'name': '<img src=x> Shared workspace', 'is_self': True,
                 'available': True, 'preferred': True}]}))

            def unavailable(route):
                requested.append(route.request.url)
                route.fulfill(status=503, json={'error': 'Workspace connection unavailable.'})

            page.route(f'**/ui/projects/{project}/board/**', unavailable)
            page.goto(f'http://127.0.0.1:{server.port}/ui/#board')
            page.screenshot(path=str(tmp_path / 'mobile-workspace-initial.png'))
            page.get_by_role('button', name='Channels and model chats').click()
            expect(page.locator('#conversation-workspace-name')).to_have_text('<img src=x> Shared workspace')
            expect(page.locator('#conversation-workspace-status')).to_have_text('Connection unavailable')
            expect(page.locator('#conversation-channels')).to_contain_text('Workspace connection unavailable.')
            assert page.locator('#conversation-sidebar img').count() == 0
            expect(page.get_by_label('Workspace', exact=True)).to_have_value(project)
            assert requested and all(f'/ui/projects/{project}/board/' in url for url in requested)
            expect(page.locator('chat-view poolhouse-board')).to_have_attribute('endpoint', f'/ui/projects/{project}/board')
            assert page.locator('chat-view poolhouse-board[endpoint="/ui/board"]').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(tmp_path / 'mobile-workspace-connection-error.png'))
            page.get_by_role('button', name='Channels and model chats').click()
            expect(page.locator('#conversation-sidebar')).to_be_hidden()
            page.screenshot(path=str(tmp_path / 'mobile-workspace-return.png'))
    finally:
        server.close()

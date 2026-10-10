"""Person Board participation and HTTP authorization over a daemon socket, answered from the person's session on the node."""

import http.client
import json
from pathlib import Path

import pytest
from launch_support import signed
from test_fleet_ui import Serving

from poolhouse import node_launch
from poolhouse.board import person
from poolhouse.fleet import routes

pytest_plugins = ["node_kit"]
pytestmark = pytest.mark.redteam


@pytest.fixture
def board(tmp_path, monkeypatch, workspace_node):
    """A signed-in daemon, the node it reaches, the worker ``builder`` and the worker's typed session."""
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    workspace_node.worker = workspace_node.member("builder")
    server = signed(Serving(tmp_path))
    try:
        yield server, workspace_node, workspace_node.session(workspace_node.worker)
    finally:
        server.close()


def post(server, doc, **options):
    return server.call('/ui/board/post', method='POST', body=doc,
                       headers={'Origin': f'http://127.0.0.1:{server.port}',
                                'Sec-Fetch-Site': 'same-origin', **options.pop('headers', {})}, **options)


def inbox(worker):
    return worker.read(inbox=True).entries


def test_local_person_posts_dm_and_thread_without_exposing_credentials(board):
    server, node, worker = board
    me = person.session(Path.cwd(), node.client)
    code, directory, _ = server.call('/ui/board/agents')
    assert code == 200 and directory['owner_id'] == me.name
    assert [(a['id'], a['role']) for a in directory['agents']] == [(node.worker.name, 'agent')]
    code, dm, _ = post(server, {'to': node.worker.name, 'body': 'Please inspect the simulation.'})
    assert code == 200
    received = inbox(worker)
    assert received[0].sender == me.name and received[0].text == 'Please inspect the simulation.'
    code, root, _ = post(server, {'to': '#general', 'body': 'Road simulation', 'subject': 'Driving'})
    assert code == 200
    code, reply, _ = post(server, {'to': '#general', 'body': 'Inspect the sensors.', 'reply_to': root['seq']})
    assert code == 200 and reply['thread'] == root['thread']
    code, thread, _ = server.call(f"/ui/board/thread?root={root['seq']}")
    assert code == 200 and len(thread['messages']) == 2
    assert me.token not in json.dumps(thread) and node.worker.token not in json.dumps(directory)
    assert dm['to'] == node.worker.name


def test_joined_session_is_required_before_the_persons_session_is_used(board, monkeypatch):
    server, _, worker = board
    monkeypatch.setattr(routes, 'in_pool', lambda _: True)
    assert post(server, {'to': 'builder', 'body': 'Unsigned'}, cookie='')[0] == 401
    cookie = server.ui.sessions.cookie_header(server.ui.sessions.open('person', 'launch-ticket'))
    assert post(server, {'to': board[1].worker.name, 'body': 'Signed'}, cookie=cookie)[0] == 200
    assert len(inbox(worker)) == 1


@pytest.mark.parametrize('attack', [
    {'headers': {'Origin': 'https://foreign.example'}},
    {'headers': {'Sec-Fetch-Site': 'cross-site'}},
    {'ui_header': False}, {'headers': {'Host': 'foreign.example'}},
])
def test_foreign_browser_cannot_send_as_person(board, attack):
    server, node, worker = board
    assert post(server, {'to': node.worker.name, 'body': 'Hostile'}, **attack)[0] in (403, 421)
    assert inbox(worker) == []


@pytest.mark.parametrize('doc', [None, [], {'to': 'builder', 'body': 'x', 'sender': 'builder'},
    {'to': 'builder', 'body': 'x', 'token': 'browser-token'},
    {'to': 'builder', 'body': 'x', 'reply_to': True}, {'to': ['builder'], 'body': 'x'}])
def test_malformed_or_impersonating_post_does_not_deliver(board, doc):
    server, _, worker = board
    assert post(server, doc)[0] == 400
    assert inbox(worker) == []


def test_page_cannot_post_announcements(board):
    server, _, _ = board
    assert post(server, {'to': '#announcements', 'body': 'Announcement'})[0] == 403


def test_oversized_post_and_unsupported_methods_do_not_mutate(board):
    server, node, worker = board
    assert post(server, {'to': node.worker.name, 'body': 'x' * 33000})[0] == 413
    assert server.call('/ui/board/post', method='DELETE')[0] == 405
    conn = http.client.HTTPConnection('127.0.0.1', server.port, timeout=5)
    try:
        conn.request('POST', '/ui/board/post', body=b'\xff', headers={
            'X-Poolhouse-UI': '1', 'Content-Type': 'application/json', 'Cookie': server.cookie,
            'Origin': f'http://127.0.0.1:{server.port}'})
        assert conn.getresponse().status == 400
    finally:
        conn.close()
    assert inbox(worker) == []


def test_ack_marks_only_an_existing_visible_message_in_its_scope(board):
    server, node, _ = board
    me = person.session(Path.cwd(), node.client)
    _, sent, _ = post(server, {'to': node.worker.name, 'body': 'Inspect this run.'})
    headers = {'Origin': f'http://127.0.0.1:{server.port}', 'Sec-Fetch-Site': 'same-origin'}
    def ack(doc):
        return server.call('/ui/board/ack', method='POST', body=doc, headers=headers)[0]
    name = node.worker.name
    assert ack({'to': name, 'through': sent['seq'] + 100}) == 403
    assert ack({'board': '#general', 'through': sent['seq']}) == 403
    assert ack({'to': name, 'through': True}) == 400
    assert me.cursor('ui:dm:' + name) == {} and me.cursor('ui:#general') == {}
    assert ack({'to': name, 'through': sent['seq']}) == 200
    assert me.cursor('ui:dm:' + name) == {'through': sent['seq']}


def test_board_extension_is_registered_in_installed_distribution_metadata(board):
    from importlib.metadata import distribution

    server, _, _ = board
    entries = [entry for entry in distribution('poolhouse').entry_points
               if entry.group == 'poolhouse.ui_routes' and entry.name == 'board']
    assert len(entries) == 1
    assert entries[0].value == 'poolhouse.workspace.fleet_routes:route'
    assert callable(entries[0].load())
    assert server.call('/ui/board/boards')[0] == 200


def test_a_response_header_with_a_line_break_is_refused():
    import pytest

    from poolhouse.fleet.routes import write

    class Handler:
        command = "GET"

        def __init__(self):
            self.sent = []

        def send_response(self, code): ...
        def send_header(self, key, value): self.sent.append((key, value))
        def end_headers(self): ...

    handler = Handler()
    with pytest.raises(ValueError):
        write(handler, 200, b"", "text/plain", {"X-A": "ok\r\nSet-Cookie: x=1"})
    assert all("\n" not in value for _, value in handler.sent)


def test_a_content_type_with_a_line_break_is_refused():
    import pytest

    from poolhouse.fleet.routes import write

    class Handler:
        command = "GET"

        def __init__(self):
            self.sent = []

        def send_response(self, code): ...
        def send_header(self, key, value): self.sent.append((key, value))
        def end_headers(self): ...

    handler = Handler()
    with pytest.raises(ValueError):
        write(handler, 200, b"", "text/plain\r\nSet-Cookie: x=1")
    assert handler.sent == []

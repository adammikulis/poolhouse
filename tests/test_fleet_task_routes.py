"""Actual Fleet sockets enforce person authority before task mutations."""

import pytest
from person_kit import person_seat

from poolhouse.board.tasks import Tasks
from poolhouse.fleet import routes

pytest_plugins = ["node_kit", "person_kit"]
pytestmark = pytest.mark.redteam


def post(server, body, **options):
    return server.call('/ui/tasks', method='POST', body=body,
                       headers={'Origin': f'http://127.0.0.1:{server.port}',
                                **options.pop('headers', {})}, **options)


def test_person_creates_native_graph_task_without_browser_credentials(tasks_ui):
    server, node = tasks_ui
    code, task, _ = post(server, {'spec': {'title': 'Replay', 'acceptance': ['Replay passes']}})
    seat = person_seat(node)
    assert code == 200 and task['created_by'] == seat.name
    assert Tasks(seat).get(task['id'])['state'] == 'queued'
    code, result, _ = server.call('/ui/tasks')
    assert code == 200 and len(result['tasks']) == 1
    assert seat.token not in str(result)


def test_a_task_made_through_the_node_is_in_the_page_and_the_page_task_is_on_the_node(tasks_ui):
    server, node = tasks_ui
    lead = node.member('lead')
    made = Tasks(node.session(lead)).create({'title': 'From the command line', 'acceptance': ['Seen']})
    code, result, _ = server.call('/ui/tasks')
    assert code == 200 and [t['id'] for t in result['tasks']] == [made['id']]
    code, shown, _ = server.call(f"/ui/tasks?id={made['id']}")
    assert code == 200 and shown['title'] == 'From the command line'
    code, page_made, _ = post(server, {'spec': {'title': 'From the page', 'acceptance': ['Seen']}})
    assert page_made['id'] in {t['id'] for t in Tasks(node.session(lead)).list()['tasks']}


@pytest.mark.parametrize('options', [{'ui_header': False},
    {'headers': {'Origin': 'https://foreign.example'}},
    {'headers': {'Sec-Fetch-Site': 'cross-site'}}])
def test_foreign_page_cannot_create_tasks(tasks_ui, options):
    server, node = tasks_ui
    assert post(server, {'spec': {'title': 'Hostile', 'acceptance': ['x']}}, **options)[0] == 403
    assert Tasks(person_seat(node)).list()['tasks'] == []


def test_joined_unsigned_and_browser_claims_are_refused(tasks_ui, monkeypatch):
    server, node = tasks_ui
    monkeypatch.setattr(routes, 'in_cluster', lambda _: True)
    assert post(server, {'spec': {'title': 'x', 'acceptance': ['x']}}, cookie='')[0] == 401
    cookie = server.ui.sessions.cookie_header(server.ui.sessions.open('person', 'launch-ticket'))
    assert post(server, {'action': 'claim', 'token': 'worker', 'allocation_id': 'fake'}, cookie=cookie)[0] == 400
    assert Tasks(person_seat(node)).list()['tasks'] == []


@pytest.mark.parametrize('body', [[], None, {'action': 'create', 'spec': None},
    {'action': 'review', 'id': '../escape', 'decision': {}}, {'spec': {}, 'identity': 'worker'}])
def test_malformed_requests_and_identity_overrides_do_not_mutate(tasks_ui, body):
    server, node = tasks_ui
    assert post(server, body)[0] == 400
    assert server.call('/ui/tasks', method='DELETE')[0] == 405
    assert Tasks(person_seat(node)).list()['tasks'] == []

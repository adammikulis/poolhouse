"""Authenticated local person access to project conversations, through the person's session on the node."""

from pathlib import Path

import pytest
from person_kit import OTHER, PROJECT, member_on

from poolhouse.board import person, session as sessions

pytest_plugins = ["node_kit", "person_kit"]


def board(call, suffix, **options):
    """A request to the person's Board route of one project."""
    return call("board/" + suffix, **options)


@pytest.fixture
def board_for(project_tasks):
    """Two projects with a worker each that wrote one message to ``#general``; ``(call, node, projects, workers)``."""
    call, node, projects = project_tasks
    workers = {}
    for ident in (PROJECT, OTHER):
        workers[ident] = member_on(node, projects[ident].root, "demo-worker")
        workers[ident].post("#general", "note", ident)
    return call, node, projects, workers


def me(node, projects, ident=PROJECT):
    return person.session(projects[ident].root, node.client)


def test_selected_project_messages_and_person_post(board_for):
    call, node, projects, _ = board_for
    status, result = board(call, "messages?board=%23general")
    assert status == 200
    assert [row["body"] for row in result["messages"]] == [PROJECT]
    status, posted = board(call, "post", method="POST", body={"to": "#general", "body": "person message"})
    assert status == 200 and posted["to"] == "#general" and posted["seq"] > 0
    status, result = board(call, "messages?board=%23general")
    assert result["messages"][-1]["body"] == "person message"
    assert result["messages"][-1]["from"] == me(node, projects).name
    assert [m.text for m in me(node, projects, OTHER).read(channel="#general", kind="message").entries] == [OTHER]


@pytest.mark.parametrize("kwargs", [{"cookie": ""}, {"ip": "192.0.2.8"},
                                   {"method": "POST", "origin": "http://evil.test",
                                    "body": {"to": "#general", "body": "hostile"}}])
def test_person_board_rejects_foreign_requests(board_for, kwargs):
    call, node, projects, _ = board_for
    assert board(call, "post" if kwargs.get("method") == "POST" else "boards", **kwargs)[0] == 403
    assert [m.text for m in me(node, projects).read(channel="#general", kind="message").entries] == [PROJECT]


def test_unknown_project_and_a_directory_that_is_no_project_never_fall_back(board_for, tmp_path):
    call, _, projects, _ = board_for
    assert board(call, "boards", project="c" * 32)[0] == 409
    projects[PROJECT].root = str(tmp_path)
    assert board(call, "boards")[0] == 403


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH"])
def test_person_board_refuses_unsupported_methods(board_for, method):
    assert board(board_for[0], "boards", method=method)[0] == 405


def test_person_threads_direct_messages_and_ack(board_for):
    call, node, projects, workers = board_for
    status, root = board(call, "post", method="POST", body={"to": "#general", "body": "root"})
    assert status == 200
    status, _reply = board(call, "post", method="POST", body={"to": "#general", "body": "reply", "reply_to": root["seq"]})
    assert status == 200
    status, thread = board(call, f"thread?root={root['seq']}")
    assert status == 200
    assert {row["body"] for row in thread["messages"]} == {"root", "reply"}
    status, listed = board(call, "threads?board=%23general")
    assert listed["threads"][0]["root"] == root["seq"] and listed["threads"][0]["replies"] == 1
    name = me(node, projects).name
    status, sent = board(call, "post", method="POST", body={"to": workers[PROJECT].name, "body": "direct"})
    assert status == 200
    status, direct = board(call, f"dm?a={name}&b={workers[PROJECT].name}")
    assert status == 200 and direct["messages"][-1]["body"] == "direct"
    assert direct["messages"][-1]["direction"] == "sent"
    status, ack = board(call, "ack", method="POST", body={"to": workers[PROJECT].name, "through": sent["seq"]})
    assert status == 200 and ack["through"] == sent["seq"]
    assert me(node, projects).cursor("ui:dm:" + workers[PROJECT].name) == {"through": sent["seq"]}
    status, chats = board(call, "dms")
    assert status == 200 and [(c["a"], c["b"], c["messages"]) for c in chats["conversations"]] \
        == [(*sorted((name, workers[PROJECT].name)), 1)]


def test_a_conversation_between_two_others_is_not_the_persons_to_read(board_for):
    call, node, projects, workers = board_for
    other = member_on(node, projects[PROJECT].root, "second-worker")
    workers[PROJECT].post(other.name, "note", "private between workers")
    assert board(call, f"dm?a={workers[PROJECT].name}&b={other.name}")[0] == 403
    status, chats = board(call, "dms")
    assert status == 200 and chats["conversations"] == []


def test_the_page_numbers_messages_in_the_boards_order_and_pages_through_them(board_for):
    call, _, _, workers = board_for
    for text in ("one", "two", "three"):
        workers[PROJECT].post("#general", "note", text)
    status, newest = board(call, "messages?board=%23general&limit=2")
    assert status == 200 and [m["body"] for m in newest["messages"]] == ["two", "three"] and newest["has_older"]
    status, older = board(call, f"messages?board=%23general&limit=2&before={newest['older']}")
    assert [m["body"] for m in older["messages"]] == [PROJECT, "one"] and not older["has_older"]
    seqs = [m["seq"] for m in older["messages"] + newest["messages"]]
    assert seqs == sorted(set(seqs))
    assert board(call, "messages?board=%23general&limit=1&after=1&before=2")[0] == 400


def test_the_wait_route_returns_when_the_board_grows(board_for):
    call, _, _, workers = board_for
    status, head = board(call, "head")
    assert status == 200
    workers[PROJECT].post("#general", "note", "news")
    status, got = board(call, f"wait?after={head['seq']}&timeout=5")
    assert status == 200 and got["seq"] > head["seq"]


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_person_post_accepts_exact_browser_origin(board_for, scheme):
    status, sent = board(board_for[0], 'post', method='POST', origin=f'{scheme}://127.0.0.1:8770',
                         body={'to': '#general', 'body': 'browser message'})
    assert status == 200 and sent['to'] == '#general'


@pytest.mark.parametrize('origin', ['https://127.0.0.1:8771', 'https://localhost:8770',
                                   'https://evil.test:8770', 'https://127.0.0.1:8770/'])
def test_person_post_rejects_different_browser_origin(board_for, origin):
    assert board(board_for[0], 'post', method='POST', origin=origin,
                 body={'to': '#general', 'body': 'foreign message'})[0] == 403


def test_announcements_are_not_the_pages_to_post(board_for):
    assert board(board_for[0], 'post', method='POST', body={'to': '#announcements', 'body': 'loud'})[0] == 403


@pytest.mark.parametrize(('length', 'expected'), [('-1', 403), ('NaN', 403), (' 2', 400),
                                               ('000000002', 400), ('1025', 400),
                                               ('99999999', 403), ('\u0661', 403)])
def test_person_connect_rejects_invalid_content_length_without_registering(board_for, length, expected):
    call, node, projects, _ = board_for
    status, _ = board(call, 'connect', method='POST', raw=b'{}', headers={'Content-Length': length})
    assert status == expected
    assert sessions.find(person.HARNESS, person.native_id(), cwd=Path(projects[PROJECT].root), client=node.client) == ''


@pytest.mark.parametrize('raw', [b'{', b'', b'null', b'[]', b'"person"', b'{"role":"human"}',
                               b'{"project_id":"foreign"}'])
def test_person_connect_rejects_invalid_json_without_registering(board_for, raw):
    call, node, projects, _ = board_for
    status, _ = board(call, 'connect', method='POST', raw=raw)
    assert status == 400
    assert sessions.find(person.HARNESS, person.native_id(), cwd=Path(projects[PROJECT].root), client=node.client) == ''


def test_person_connect_registers_the_persons_session_once(board_for):
    call, node, projects, _ = board_for
    first = board(call, 'connect', method='POST', body={})
    second = board(call, 'connect', method='POST', body={})
    assert first[0] == second[0] == 200 and first[1] == second[1] and first[1]['name'] == me(node, projects).name


def test_person_connect_is_an_explicit_post_and_holds_no_token_for_the_page(board_for):
    call, node, projects, _ = board_for
    assert board(call, 'connect')[0] == 405
    assert sessions.find(person.HARNESS, person.native_id(), cwd=Path(projects[PROJECT].root), client=node.client) == ''
    status, answer = board(call, 'connect', method='POST', body={})
    assert status == 200 and set(answer) == {'connected', 'name'}
    assert me(node, projects).token not in str(answer)

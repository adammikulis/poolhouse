"""The person's Board page over a real node: what the route serves, the one write it takes, and its live feed."""

from __future__ import annotations

import http.client
import json
import threading
import time
from pathlib import Path

import pytest

from poolhouse import node_launch
from poolhouse.board import client, person
from poolhouse.workspace import boardroute

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node, monkeypatch):
    node = workspace_node
    monkeypatch.setattr(node_launch, "default_state", lambda: node.state)
    node.alice, node.bob = node.member("alice"), node.member("bob")
    node.sessions = {"alice": node.session(node.alice), "bob": node.session(node.bob)}
    return node


@pytest.fixture
def route(team):
    """``call(path, method, headers, body)`` against a listener serving the person's session; ``call.me`` is it."""
    server = boardroute.serve(lambda: person.session(Path.cwd(), team.client))
    server.start()
    person.session(Path.cwd(), team.client)

    def call(path, method="GET", headers=None, body=None, json_body=True):
        head = {"Cookie": f"ml_session={server.session}", **(headers or {})}
        if body is not None and json_body:
            head.setdefault("Content-Type", "application/json")
            body = json.dumps(body)
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=30)
        conn.request(method, path, body=body, headers=head)
        r = conn.getresponse()
        out = r.status, dict(r.getheaders()), r.read()
        conn.close()
        return out

    call.port = server.port
    call.origin = {"Origin": f"http://127.0.0.1:{server.port}"}
    call.me = person.session(Path.cwd(), team.client)
    yield call
    server.stop()


def test_the_route_serves_the_person_every_room_and_conversation_as_plain_json(team, route):
    alice, bob = team.sessions["alice"], team.sessions["bob"]
    alice.room("create", "#ops")
    root = alice.post("#ops", "note", "x‮<img src=x onerror=alert(1)>" + "y" * 9000, subject="<b>s</b>")
    route.me.post(team.alice.name, "note", "dm text")
    alice.post(route.me.name, "note", "dm back")
    status, headers, body = route("/board/boards")
    assert status == 200 and "no-store" in headers["Cache-Control"]
    assert sorted(b["name"] for b in json.loads(body)["boards"]) == ["#announcements", "#general", "#ops"]
    _, _, body = route("/board/messages?board=%23ops")
    msg = json.loads(body)["messages"][0]
    assert msg["truncated"] and len(msg["body"]) == 4000 and "‮" not in msg["body"]
    assert msg["subject"] == "<b>s</b>" and msg["from"] == team.alice.name
    number = msg["seq"]
    assert json.loads(route(f"/board/thread?root={number}")[2])["messages"][0]["seq"] == number
    assert json.loads(route("/board/threads?board=%23ops")[2])["threads"][0]["root"] == number
    assert len(json.loads(route(f"/board/dm?a={route.me.name}&b={team.alice.name}")[2])["messages"]) == 2
    assert json.loads(route("/board/dms")[2])["conversations"][0]["messages"] == 2
    everything = b"".join(route(p)[2] for p in ("/board/boards", "/board/messages?board=%23ops"))
    for secret in (route.me.token, team.alice.token, team.bob.token):
        assert secret.encode() not in everything
    assert route.me.token.encode() not in route("/")[2]
    assert root["id"] and bob.name == team.bob.name


def test_looking_at_an_open_room_joins_the_person_to_it_and_a_private_room_stays_closed(team, route):
    alice = team.sessions["alice"]
    alice.room("create", "#open")
    alice.room("create", "#vault", private=True)
    alice.post("#open", "note", "open words")
    alice.post("#vault", "note", "SECRETROOM")
    assert [m["body"] for m in json.loads(route("/board/messages?board=%23open")[2])["messages"]] == ["open words"]
    assert next(r for r in route.me.rooms() if r["name"] == "#open")["member"]
    assert "SECRET" not in route("/board/messages?board=%23vault")[2].decode()
    assert route("/board/post", "POST", route.origin, {"to": "#vault", "body": "forged"})[0] == 403


def test_the_route_reads_only_and_checks_host_origin_and_fetch_site(team, route):
    for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
        assert route("/board/boards", method)[0] in (405, 501)
    assert route("/board/boards", headers={"Host": "evil.example"})[0] == 421
    assert route("/board/boards", headers={"Host": f"localhost:{route.port + 1}"})[0] == 421
    assert route("/board/boards", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert route("/board/boards", headers={"Origin": "http://evil.example"})[0] == 403
    assert route("/board/boards", headers={"Sec-Fetch-Site": "same-origin"})[0] == 200
    assert route("/board/boards", headers={"Origin": f"http://127.0.0.1:{route.port}"})[0] == 200
    assert route("/board/messages?board=%23..%2Fx")[0] == 400
    assert route("/board/messages?board=%23nothing")[0] in (400, 403)
    assert route("/board/unknown")[0] == 400
    assert route("/ui/ml-ui/../daemon.py")[0] == 404


def test_the_route_without_a_signed_in_person_answers_401_and_in_no_project_403(team):
    port = 1
    session = lambda: person.session(Path.cwd(), team.client)  # noqa: E731
    assert boardroute.respond(session, boardroute.Request("GET", "/board/boards", {"host": f"127.0.0.1:{port}"},
                                                       port, signed_in=False))[0] == 401

    def nowhere():
        raise client.Denied("denied", "this directory is not part of a project")
    assert boardroute.respond(nowhere, boardroute.Request("GET", "/board/boards", {"host": f"127.0.0.1:{port}"},
                                                          port))[0] == 403


def test_the_person_posts_to_a_room_a_thread_and_a_conversation_from_the_page(team, route):
    alice = team.sessions["alice"]
    alice.room("create", "#ops")
    route.me.room("join", "#ops")
    alice.post("#ops", "note", "start", subject="s")
    root = json.loads(route("/board/messages?board=%23ops")[2])["messages"][0]["seq"]
    status, _, body = route("/board/post", "POST", route.origin, {"to": "#ops", "body": "<img src=x> hi", "subject": "from me"})
    assert status == 200 and json.loads(body)["to"] == "#ops"
    status, _, body = route("/board/post", "POST", route.origin, {"to": "#ops", "body": "a reply", "reply_to": root})
    assert status == 200 and json.loads(body)["thread"] == root
    assert route("/board/post", "POST", route.origin, {"to": team.alice.name, "body": "hello alice"})[0] == 200
    posted = alice.read(channel="#ops", kind="message").entries
    assert [m.text for m in posted if m.sender == route.me.name] == ["<img src=x> hi", "a reply"]
    assert [m.text for m in alice.read(inbox=True).entries][-1] == "hello alice"
    assert route.me.name in next(r for r in alice.rooms() if r["name"] == "#ops")["members"]


def test_the_post_route_refuses_a_foreign_page_a_wrong_type_a_bad_body_and_other_paths(team, route):
    doc = {"to": "#general", "body": "x"}
    before = route.me.status()["entries"]
    cross = {"Origin": "http://attacker.example"}
    assert route("/board/post", "POST", cross, doc)[0] in (403, 421)
    assert route("/board/post", "POST", {}, doc)[0] == 403
    assert route("/board/post", "POST", {**route.origin, "Sec-Fetch-Site": "cross-site"}, doc)[0] == 403
    assert route("/board/post", "POST", {**route.origin, "Host": "attacker.example"}, doc)[0] == 421
    assert route("/board/post", "POST", {**route.origin, "Content-Type": "text/plain"}, json.dumps(doc),
                 json_body=False)[0] == 400
    for bad in ({"to": "#general"}, {"to": "#general", "body": "   "}, {"to": 3, "body": "x"},
                {"to": "#general", "body": "x", "reply_to": "7"}, {"to": "#general", "body": "x", "type": "nope"},
                {"to": "#nothing", "body": "x"}, {"to": "nobody", "body": "x"}, [1], "x"):
        assert route("/board/post", "POST", route.origin, bad)[0] in (400, 403), bad
    assert route("/board/post", "POST", route.origin, {"to": "#general", "body": "x" * 40000})[0] == 400
    for path in ("/board/boards", "/board/subscribe", "/board/join", "/", "/board/post/x"):
        assert route(path, "POST", route.origin, doc)[0] in (405, 501)
    assert route("/board/post", "PUT", route.origin, doc)[0] in (405, 501)
    assert route.me.status()["entries"] == before


def test_the_live_route_answers_the_moment_a_message_arrives_and_times_out_quietly(team, route):
    alice = team.sessions["alice"]
    seq = json.loads(route("/board/head")[2])["seq"]
    began = time.monotonic()
    got = json.loads(route(f"/board/wait?after={seq}&timeout=1")[2])
    assert got["seq"] == seq and 0.8 < time.monotonic() - began < 5
    alice.room("create", "#ops")
    seq = json.loads(route("/board/head")[2])["seq"]
    pushed = threading.Timer(0.4, lambda: alice.post("#ops", "note", "pushed"))
    pushed.start()
    began = time.monotonic()
    try:
        woke = json.loads(route(f"/board/wait?after={seq}&timeout=20")[2])
    finally:
        pushed.join()
    assert woke["seq"] > seq and time.monotonic() - began < 3
    posted = threading.Timer(0.3, lambda: alice.post(team.bob.name, "note", "a private join"))
    posted.start()
    began = time.monotonic()
    try:
        join = json.loads(route(f"/board/wait?after={woke['seq']}&timeout=20")[2])
    finally:
        posted.join()
    assert join["seq"] > woke["seq"] and time.monotonic() - began < 3


def test_the_person_wakes_within_a_tenth_of_a_second(team, route):
    alice = team.sessions["alice"]
    alice.post(team.bob.name, "note", "warm up")
    seq = json.loads(route("/board/head")[2])["seq"]
    lat = []

    def ask(after, out):
        out.update(got=json.loads(route(f"/board/wait?after={after}&timeout=20")[2]), at=time.monotonic())

    for i in range(5):
        out: dict = {}
        thread = threading.Thread(target=ask, args=(seq, out))
        thread.start()
        time.sleep(0.3)
        alice.post(team.bob.name, "note", f"ping {i}")
        began = time.monotonic()
        thread.join(15)
        lat.append(max(0.0, out["at"] - began) * 1000)
        seq = out["got"]["seq"]
    lat.sort()
    print(f"person long poll: median {lat[len(lat) // 2]:.1f} ms, max {lat[-1]:.1f} ms")
    assert lat[len(lat) // 2] < 100


def test_a_message_over_the_pages_size_is_refused_before_the_node_hears_of_it(team, route):
    status, _, _ = route("/board/post", "POST", route.origin, {"to": "#general", "body": "x" * 40000})
    assert status == 400
    assert route.me.read(channel="#general", kind="message").entries == []

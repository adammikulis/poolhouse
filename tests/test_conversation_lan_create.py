"""A person signed in from a LAN address starts conversations; nobody else does."""
from __future__ import annotations

import pytest
from test_fleet_ui import WORDS, Serving

from poolhouse.fleet.conversations import Conversations
from tests.pool_support import join_pool_by_passphrase
from tests.remote_client_support import REMOTE_ADDRESS

OPERATION = "a" * 32
BODY = {"model": "m", "settings": {}, "operation_id": OPERATION}


@pytest.fixture
def lan(tmp_path):
    served = Serving(tmp_path)
    try:
        join_pool_by_passphrase(WORDS, group="home", path=served.keyfile)
        served.call("/ui/setup/join", method="POST", body={"passphrase": WORDS, "group": "home"})
        served.ui.setup_finished()
        served.ui.root = tmp_path
        served.ui.conversations = Conversations(tmp_path / "chats")
        yield served
    finally:
        served.close()


def sign_in(served) -> str:
    _, _, headers = served.call("/ui/session", method="POST", body={"passphrase": WORDS}, host=REMOTE_ADDRESS)
    return headers["Set-Cookie"].split(";")[0]


def create(served, cookie, *, host=REMOTE_ADDRESS, origin="same", body=BODY, **headers):
    served.call("/ui/conversations/defaults", host=REMOTE_ADDRESS)           # brings the remote listener up
    port = served.lan_httpd.server_port
    sent = {"Origin": f"http://{host}:{port}" if origin == "same" else origin, **headers}
    return served.call("/ui/conversations", method="POST", body=body, host=host, cookie=cookie, headers=sent)


def test_a_signed_in_window_on_a_lan_address_starts_a_conversation(lan):
    status, made, _ = create(lan, sign_in(lan))
    assert status == 201, made
    assert lan.ui.conversations.get(made["id"]) is not None


def test_the_same_request_is_idempotent_for_the_session_that_made_it(lan):
    cookie = sign_in(lan)
    first = create(lan, cookie)
    again = create(lan, cookie)
    assert (first[0], again[0]) == (201, 201) and first[1]["id"] == again[1]["id"]
    assert len(lan.ui.conversations.search("")) == 1


def test_a_machine_on_the_network_without_a_session_starts_nothing(lan):
    status, _, _ = create(lan, "")
    assert status in (401, 403)
    assert lan.ui.conversations.search("") == []


def test_another_origin_is_refused(lan):
    status, _, _ = create(lan, sign_in(lan), origin="http://evil.example")
    assert status == 403
    assert lan.ui.conversations.search("") == []


def test_a_cross_site_request_is_refused(lan):
    status, _, _ = create(lan, sign_in(lan), **{"Sec-Fetch-Site": "cross-site"})
    assert status in (401, 403)
    assert lan.ui.conversations.search("") == []


def test_a_hostname_in_place_of_the_address_is_refused(lan):
    status, _, _ = create(lan, sign_in(lan), host="rebound.example.com")
    assert status in (401, 403)
    assert lan.ui.conversations.search("") == []


def test_a_script_without_the_page_header_is_refused(lan):
    cookie = sign_in(lan)
    lan.call("/ui/conversations/defaults", host=REMOTE_ADDRESS)
    status, _, _ = lan.call("/ui/conversations", method="POST", body=BODY, host=REMOTE_ADDRESS, cookie=cookie,
                            ui_header=False, headers={"Origin": f"http://{REMOTE_ADDRESS}:{lan.lan_httpd.server_port}"})
    assert status in (401, 403)
    assert lan.ui.conversations.search("") == []


def test_a_token_session_starts_nothing(lan):
    token = lan.ui.sessions.open("token", "token")
    status, _, _ = create(lan, lan.ui.sessions.cookie_header(token).split(";")[0])
    assert status in (401, 403)
    assert lan.ui.conversations.search("") == []


def test_a_second_session_cannot_take_the_first_ones_operation(lan):
    mine, theirs = sign_in(lan), sign_in(lan)
    assert create(lan, mine)[0] == 201
    assert create(lan, theirs)[0] in (403, 409)
    status, _, _ = lan.call(f"/ui/conversations/operations/{OPERATION}", host=REMOTE_ADDRESS, cookie=theirs,
                            headers={"Origin": f"http://{REMOTE_ADDRESS}:{lan.lan_httpd.server_port}"})
    assert status == 404
    assert len(lan.ui.conversations.search("")) == 1

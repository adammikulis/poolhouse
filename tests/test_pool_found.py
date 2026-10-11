"""An agent founds a pool for a person: the node as shipped, a key the test made that stands for the person, and the person's answers given the way a person gives them.

The person's yes is typed at a pseudo-terminal the node itself wrote its question on (the node never answers for anyone); the phone's tap is a
decision signed by a key the test made. What the tests check is that nothing is founded without the person's key, and that everything the person
sees is a sentence an agent can pass on.
"""

from __future__ import annotations

import os
import pty
import threading
import time
from contextlib import contextmanager

import pytest
from phone_kit import add_phone, endpoint_of
from pool_check import wire

from poolhouse.board import founding, presence, session as board_session
from poolhouse.board.client import Denied
from poolhouse.board.founding import Admin, Refusal
from poolhouse.fleet.onboard.manifest import Signer

pytest_plugins = ["node_kit", "phone_kit"]


def pool_session(node) -> board_session.Session:
    """The session an agent founds a pool as: its own token on the pool board, granted nothing."""
    made = board_session.register(node.client, "pool", board_session.Native("claude-sonnet-5-5", "claude-code", "pool-agent"), "")
    return board_session.Session(node.client, "pool", made.token, made.name)


def person(strength: str = "strong") -> Admin:
    """The person's key, in memory, for this test alone."""
    return Admin(Signer.generate(), strength)


@contextmanager
def person_at_a_terminal(yes: bool = True):
    """A terminal of the test's own that the node writes its question on; the person types yes when it appears, or nobody does."""
    leader, follower = pty.openpty()

    def answer() -> None:
        if wire._screen_says(leader, "Type yes", 20.0):
            os.write(leader, b"yes\n")

    if yes:
        threading.Thread(target=answer, daemon=True).start()
    try:
        yield presence.Voice(terminal=os.ttyname(follower))
    finally:
        os.close(leader)
        os.close(follower)


def governance(node) -> dict:
    return node.client.call("pool_status")["governance"]


def test_an_agent_founds_a_pool_when_the_person_says_yes_and_a_second_try_says_it_is_there(workspace_node):
    session, admin = pool_session(workspace_node), person()
    assert governance(workspace_node)["mode"] == "unfounded"
    with person_at_a_terminal() as voice:
        made = founding.found(session, admin, "home", "secure", founding.Terms(voice=voice))
    assert (made.name, made.policy, made.epoch, made.already) == ("home", "secure", 1, False)
    gov = governance(workspace_node)
    assert gov["mode"] == "governed" and gov["name"] == "home" and gov["admins"][0]["strength"] == "strong"
    members = workspace_node.client.call("pool_status")["members"]
    assert [m["status"] for m in members if m["self"]] == ["active"], "this device is the pool's first member"
    again = founding.found(session, admin, "home", "secure")
    assert again.already and again.pool == made.pool and "already in the pool home" in again.say()
    assert founding.found(session, admin, "another", "open").pool == made.pool, "a founded device does not found a second pool"
    audit = session.read_all(channel="#audit")
    assert any("pool_created" in str(e) for e in audit), "founding is on the pool board's audit"


def test_nobody_says_yes_and_no_pool_and_no_key_exist(workspace_node, monkeypatch):
    monkeypatch.setattr(presence, "WAIT_S", 1.0)
    session = pool_session(workspace_node)
    with person_at_a_terminal(yes=False) as voice, pytest.raises(Refusal) as refused:
        founding.found(session, person(), "home", "secure", founding.Terms(voice=voice))
    assert "Nobody said yes" in refused.value.say and refused.value.then == "stop"
    assert governance(workspace_node)["mode"] == "unfounded"
    assert workspace_node.client.call("person_keys")["keys"] == [], "a key nobody answered for was not enrolled"


def test_a_key_that_asks_nothing_of_the_person_each_time_is_refused_before_anyone_is_asked(workspace_node):
    session = pool_session(workspace_node)
    with pytest.raises(Refusal) as refused:
        founding.found(session, person("weak"), "home")
    assert "your phone" in refused.value.say and refused.value.needs == "device" and len(refused.value.say) <= 160
    assert workspace_node.client.call("person_keys")["keys"] == [] and governance(workspace_node)["mode"] == "unfounded"


def test_a_charter_the_agent_made_without_the_persons_key_founds_nothing(workspace_node):
    session = pool_session(workspace_node)
    admin = person()
    key = [{"key": admin.key.public.hex(), "person": "owner", "strength": "strong", "label": "the owner's key"}]
    draft = workspace_node.client.call("pool_draft_found", "", session.token, title="home", policy="secure", project="", keys=key)
    with pytest.raises(Denied, match=r"grant|accept|denied"):
        workspace_node.client.call("pool_found", "", session.token, charter=draft["charter"])
    assert governance(workspace_node)["mode"] == "unfounded", "a charter nobody's key accepted, asked for by a session nobody granted, is no pool"


def test_the_phone_is_asked_for_the_grant_and_the_pool_is_founded_when_the_person_taps(phone_node):
    node = phone_node
    phone = add_phone(node)
    session, admin = pool_session(node), person()
    told: list[str] = []
    result: dict = {}

    def agent() -> None:
        try:
            result["made"] = founding.found(session, admin, "home", "secure", founding.Terms(presence.Voice(say=told.append), 30))
        except Refusal as err:  # reported below
            result["error"] = err

    thread = threading.Thread(target=agent)
    thread.start()
    endpoint, seen = endpoint_of(node), None
    for _ in range(100):
        waiting = endpoint.ok("list", {}, token=phone.token)
        if waiting["approvals"]:
            seen = endpoint.ok("get", {"id": waiting["approvals"][0]["id"]}, token=phone.token)
            break
        time.sleep(0.1)
    assert seen is not None, "the agent asked the phone"
    while not told:
        time.sleep(0.05)
    assert endpoint.ok("decide", phone.decision(seen, "approve", pick=told[0].rsplit(" ", 1)[1].rstrip(".")))["state"] == "approved"
    thread.join(30)
    assert "error" not in result, result
    assert result["made"].name == "home" and governance(node)["mode"] == "governed"


def test_the_phone_says_no_and_the_agent_has_a_sentence_and_nothing_is_founded(phone_node):
    node = phone_node
    phone = add_phone(node)
    session = pool_session(node)
    outcome: list = []
    told: list[str] = []
    thread = threading.Thread(target=lambda: outcome.append(_tried(session, told)))
    thread.start()
    endpoint, seen = endpoint_of(node), None
    for _ in range(100):
        waiting = endpoint.ok("list", {}, token=phone.token)
        if waiting["approvals"]:
            seen = endpoint.ok("get", {"id": waiting["approvals"][0]["id"]}, token=phone.token)
            break
        time.sleep(0.1)
    assert seen is not None
    assert endpoint.ok("decide", phone.decision(seen, "deny"))["state"] == "denied"
    thread.join(30)
    assert isinstance(outcome[0], Refusal) and "You said no" in outcome[0].say and governance(node)["mode"] == "unfounded"


def _tried(session, told):
    try:
        return founding.found(session, person(), "home", "secure", founding.Terms(presence.Voice(say=told.append), 30))
    except Refusal as refused:
        return refused



def test_the_command_for_agents_says_one_sentence_and_exits_3_when_the_person_has_something_to_do(workspace_node):
    import json

    done = workspace_node.cli("pool", "found", "home", "--json", who=workspace_node.member("agent"))
    said = json.loads(done.stdout.strip().splitlines()[-1])
    assert done.returncode == 3 and said["founded"] is False and "phone" in said["say"] and said["needs"] == "device", done
    assert governance(workspace_node)["mode"] == "unfounded"

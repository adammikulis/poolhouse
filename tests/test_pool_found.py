"""An agent founds a pool for a person: the node as shipped, a key the test made that stands for the person, and the person's answers given the way a person gives them.

The key is strong only where the node holds it as a phone's: the test writes it into the node's person keys file marked as a phone's own
enrolment marks it (`enrol_as_phone_key`); the phone's tap is a decision signed by a key the test made. What the tests check is that
nothing is founded without the person's key, that a key the node does not hold as a phone's is never strong, and that everything the
person sees is a sentence an agent can pass on.
"""

from __future__ import annotations

import threading
import time

import pytest
from node_kit import enrol_as_phone_key, enrol_first
from phone_kit import add_phone, endpoint_of

from poolhouse.board import founding, presence, session as board_session
from poolhouse.board.client import Denied, Invalid
from poolhouse.board.founding import Admin, Refusal
from poolhouse.fleet.onboard.manifest import Signer

pytest_plugins = ["node_kit", "phone_kit"]


def pool_session(node) -> board_session.Session:
    """The session an agent founds a pool as: its own token on the pool board, granted nothing."""
    made = board_session.register(node.client, "pool", board_session.Native("claude-sonnet-5-5", "claude-code", "pool-agent"), "")
    return board_session.Session(node.client, "pool", made.token, made.name)


def person() -> Admin:
    """The person's key, in memory, for this test alone; the node holds it as a phone's only where the test enrols it."""
    return Admin(Signer.generate())


def governance(node) -> dict:
    return node.client.call("pool_status")["governance"]


def test_an_agent_founds_a_pool_of_a_key_a_phone_enrolled_and_a_second_try_says_it_is_there(workspace_node):
    admin = person()
    enrol_as_phone_key(workspace_node, admin.key)
    session = pool_session(workspace_node)
    assert governance(workspace_node)["mode"] == "unfounded"
    made = founding.found(session, admin, "home", "secure")
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


class Watching:
    """A person's key that remembers the bytes it was asked to sign."""

    def __init__(self, key: Signer) -> None:
        self.key, self.asked = key, []

    @property
    def public(self) -> bytes:
        return self.key.public

    def sign_bytes(self, data: bytes) -> bytes:
        self.asked.append(data)
        return self.key.sign_bytes(data)


def test_founding_asks_the_persons_key_for_the_charter_once_and_for_nothing_after_it(workspace_node):
    key = Watching(Signer.generate())
    enrol_as_phone_key(workspace_node, key.key)
    session = pool_session(workspace_node)
    founding.found(session, Admin(key), "home", "secure")
    about_the_pool = [data.split(b"\n", 1)[0] for data in key.asked if data.startswith(b"poolhouse/pool-")]
    assert about_the_pool == [b"poolhouse/pool-accept/v2"], "the acceptance of the charter is the only signature about the pool"
    me = next(m for m in workspace_node.client.call("pool_status")["members"] if m["self"])
    assert (me["status"], me["by"]) == ("active", "found"), "this device is a member by the charter, not by an admit"


def test_the_draft_names_this_device_as_the_pools_first_member_and_the_card_says_so(workspace_node):
    admin = person()
    enrol_as_phone_key(workspace_node, admin.key)
    session = pool_session(workspace_node)
    key = [{"key": admin.key.public.hex(), "person": "owner", "label": "the owner's key"}]
    draft = workspace_node.client.call("pool_draft_found", "", session.token, title="home", policy="secure", project="", keys=key)
    assert draft["charter"]["first"]["cert"] == (workspace_node.state / "device.cert").read_bytes().hex()
    assert "put the computer" in draft["summary"] and "'home'" in draft["summary"], draft["summary"]


def test_nobody_taps_the_phone_and_no_pool_and_no_key_exist(phone_node):
    add_phone(phone_node)
    admin = person()
    enrol_as_phone_key(phone_node, admin.key)
    session = pool_session(phone_node)
    held = phone_node.client.call("person_keys")["keys"]
    with pytest.raises(Refusal) as refused:
        founding.found(session, admin, "home", "secure", founding.Terms(wait_s=1.0))
    assert refused.value.then == "wait" and refused.value.needs == "approval" and len(refused.value.say) <= 160
    assert governance(phone_node)["mode"] == "unfounded"
    assert phone_node.client.call("person_keys")["keys"] == held, "a card nobody answered enrolled no key"


def test_a_key_the_node_does_not_hold_as_a_phones_is_refused_before_anyone_is_asked(workspace_node):
    session = pool_session(workspace_node)
    with pytest.raises(Refusal) as refused:
        founding.found(session, person(), "home")
    assert "your phone" in refused.value.say and refused.value.needs == "device" and len(refused.value.say) <= 160
    assert workspace_node.client.call("person_keys")["keys"] == [] and governance(workspace_node)["mode"] == "unfounded"


def test_a_key_the_person_typed_yes_for_is_not_strong_whatever_the_agent_says(workspace_node):
    admin = person()
    enrol_first(workspace_node.client, workspace_node.member("agent").token, admin.key)
    session = pool_session(workspace_node)
    with pytest.raises(Refusal) as refused:
        founding.found(session, admin, "home")
    assert "your phone" in refused.value.say and governance(workspace_node)["mode"] == "unfounded"
    key = [{"key": admin.key.public.hex(), "person": "owner", "strength": "strong", "label": "the owner's key"}]
    with pytest.raises(Invalid):
        workspace_node.client.call("pool_draft_found", "", session.token, title="home", policy="secure", project="", keys=key)
    plain = dict(key[0])
    del plain["strength"]
    drafted = workspace_node.client.call("pool_draft_found", "", session.token, title="home", policy="secure", project="", keys=[plain])
    assert [k["strength"] for k in drafted["charter"]["admins"]] == ["weak"]


def test_a_charter_the_agent_made_without_the_persons_key_founds_nothing(workspace_node):
    admin = person()
    enrol_as_phone_key(workspace_node, admin.key)
    session = pool_session(workspace_node)
    key = [{"key": admin.key.public.hex(), "person": "owner", "label": "the owner's key"}]
    draft = workspace_node.client.call("pool_draft_found", "", session.token, title="home", policy="secure", project="", keys=key)
    with pytest.raises(Denied, match=r"grant|accept|denied"):
        workspace_node.client.call("pool_found", "", session.token, charter=draft["charter"])
    assert governance(workspace_node)["mode"] == "unfounded", "a charter nobody's key accepted, asked for by a session nobody granted, is no pool"


def test_the_phone_is_asked_for_the_grant_and_the_pool_is_founded_when_the_person_taps(phone_node):
    node = phone_node
    phone = add_phone(node)
    admin = person()
    enrol_as_phone_key(node, admin.key)
    session = pool_session(node)
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
    admin = person()
    enrol_as_phone_key(node, admin.key)
    session = pool_session(node)
    outcome: list = []
    told: list[str] = []
    thread = threading.Thread(target=lambda: outcome.append(_tried(session, told, admin)))
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


def _tried(session, told, admin):
    try:
        return founding.found(session, admin, "home", "secure", founding.Terms(presence.Voice(say=told.append), 30))
    except Refusal as refused:
        return refused



def test_the_command_for_agents_says_one_sentence_and_exits_3_when_the_person_has_something_to_do(workspace_node):
    import json

    done = workspace_node.cli("pool", "found", "home", "--json", who=workspace_node.member("agent"))
    said = json.loads(done.stdout.strip().splitlines()[-1])
    assert done.returncode == 3 and said["founded"] is False and "phone" in said["say"] and said["needs"] == "device", done
    assert governance(workspace_node)["mode"] == "unfounded"

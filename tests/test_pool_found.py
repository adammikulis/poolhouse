"""An agent founds a pool for a person: the node as shipped, a key the test made that stands for the person, and nobody asked.

The key is strong only where the node holds it as a phone's: the test writes it into the node's person keys file marked as a phone's own
enrolment marks it (`enrol_as_phone_key`); a phone's decision is signed by a key the test made. What the tests check is that nothing is
founded without the key the charter names, that a key the node does not hold is never strong, that a key enrolled through the node's own secret
is held at the level the node recorded whatever the agent says, that nothing waits for a phone or a person (a founding that cannot go on says
so at once, in one sentence), and that a phone's own decision still founds the pool.
"""

from __future__ import annotations

import json
import time

import pytest
from node_kit import enrol_as_phone_key, enrol_first
from phone_kit import add_phone, endpoint_of

from poolhouse.board import approval, founding, session as board_session
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
    assert gov["mode"] == "governed" and gov["name"] == "home" and gov["admins"][0]["claim"]["method"] == "phone-keystore" and gov["admins"][0]["counted"] == "computer"
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
    assert about_the_pool == [b"poolhouse/pool-accept/v3"], "the acceptance of the charter is the only signature about the pool"
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


def test_a_node_whose_only_key_is_a_phones_enrols_this_devices_key_beside_it_and_founding_waits_for_nothing(phone_node):
    add_phone(phone_node)
    admin = person()
    enrol_as_phone_key(phone_node, admin.key)
    session = pool_session(phone_node)
    held = phone_node.client.call("person_keys")["keys"]
    began = time.monotonic()
    made = founding.found(session, admin, "home", "secure")
    assert time.monotonic() - began < 20, "nobody was waited for: no phone tap"
    assert (made.name, made.already) == ("home", False)
    gov = governance(phone_node)
    assert gov["mode"] == "governed" and gov["admins"][0]["claim"]["method"] == "phone-keystore"
    after = phone_node.client.call("person_keys")["keys"]
    assert len(after) == len(held) + 1 and {k["fingerprint"] for k in held} < {k["fingerprint"] for k in after}, "this device's own key joined the phone's"


def test_a_key_the_node_did_not_hold_counts_for_nothing_when_it_is_enrolled_by_founding_and_no_pool_is_made(workspace_node):
    session = pool_session(workspace_node)
    admin = person()
    with pytest.raises(Refusal) as refused:
        founding.found(session, admin, "home")
    assert "counts for nothing" in refused.value.say and "this computer makes its own" in refused.value.say and refused.value.needs == "none" and len(refused.value.say) <= 260
    assert governance(workspace_node)["mode"] == "unfounded"
    key = [{"key": admin.key.public.hex(), "person": "owner", "label": "the owner's key"}]
    drafted = workspace_node.client.call("pool_draft_found", "", session.token, title="home", policy="secure", project="", keys=key)
    assert [k["claim"]["method"] for k in drafted["charter"]["admins"]] == ["keystore-typed"], "the node recorded the key as the typed yes it is"


def test_a_key_enrolled_through_the_nodes_own_secret_is_a_typed_yes_whatever_the_agent_says_and_founds_no_pool(workspace_node):
    admin = person()
    enrol_first(workspace_node.client, workspace_node.member("agent").token, admin.key)
    session = pool_session(workspace_node)
    with pytest.raises(Refusal) as refused:
        founding.found(session, admin, "home")
    assert "counts for nothing" in refused.value.say, "this computer can make a better key than a typed yes, so a typed one does not found a pool"
    assert governance(workspace_node)["mode"] == "unfounded"
    key = [{"key": admin.key.public.hex(), "person": "owner", "claim": "phone-keystore", "label": "the owner's key"}]
    with pytest.raises(Invalid):
        workspace_node.client.call("pool_draft_found", "", session.token, title="home", policy="secure", project="", keys=key)
    plain = dict(key[0])
    del plain["claim"]
    drafted = workspace_node.client.call("pool_draft_found", "", session.token, title="home", policy="secure", project="", keys=[plain])
    assert [k["claim"]["method"] for k in drafted["charter"]["admins"]] == ["keystore-typed"], "an agent cannot name the level of a key: the node says how it holds it"
    assert drafted["counting"] is False


def test_a_charter_the_agent_made_without_the_persons_key_founds_nothing(workspace_node):
    admin = person()
    enrol_as_phone_key(workspace_node, admin.key)
    session = pool_session(workspace_node)
    key = [{"key": admin.key.public.hex(), "person": "owner", "label": "the owner's key"}]
    draft = workspace_node.client.call("pool_draft_found", "", session.token, title="home", policy="secure", project="", keys=key)
    with pytest.raises(Denied, match=r"grant|accept|denied"):
        workspace_node.client.call("pool_found", "", session.token, charter=draft["charter"])
    assert governance(workspace_node)["mode"] == "unfounded", "a charter nobody's key accepted, asked for by a session nobody granted, is no pool"


def test_a_phones_own_decision_still_founds_the_pool_and_founding_then_goes_on_with_the_grant_it_gave(phone_node):
    node = phone_node
    phone = add_phone(node)
    admin = person()
    enrol_as_phone_key(node, admin.key)
    session = pool_session(node)
    asked = approval.request(session, approval.wire([approval.Ask("pool_create", "home", lasts="10m", uses=1)], note="founding the pool home"))
    assert asked.state == approval.PENDING, "the request waits for a key: nothing answers it for the phone"
    endpoint = endpoint_of(node)
    seen = endpoint.ok("get", {"id": asked.id}, token=phone.token)
    decision = phone.decision(seen, "approve", pick=asked.code) if asked.code else phone.decision(seen, "approve")
    assert endpoint.ok("decide", decision)["state"] == "approved"
    made = founding.found(session, admin, "home", "secure")
    assert made.name == "home" and governance(node)["mode"] == "governed"


def test_the_command_for_agents_founds_the_pool_at_once_says_one_sentence_and_says_it_is_there_the_second_time(workspace_node):
    agent = workspace_node.member("agent")
    done = workspace_node.cli("pool", "found", "home", "--json", who=agent, env={"CLAUDECODE": "1"})
    said = json.loads(done.stdout.strip().splitlines()[-1])
    assert done.returncode == 0 and said["founded"] is True and said["name"] == "home" and "now in the pool home" in said["say"], done
    gov = governance(workspace_node)
    assert gov["mode"] == "governed" and gov["name"] == "home"
    again = json.loads(workspace_node.cli("pool", "found", "home", "--json", who=agent, env={"CLAUDECODE": "1"}).stdout.strip().splitlines()[-1])
    assert again["founded"] is False and "already in the pool home" in again["say"]


def test_the_command_for_agents_says_one_sentence_and_exits_3_when_this_computer_cannot_keep_the_key(workspace_node):
    agent = workspace_node.member("agent")
    done = workspace_node.cli("pool", "found", "home", "--json", who=agent, env={"CLAUDECODE": "1", "POOLHOUSE_SIGNING_PASSPHRASE": ""})
    said = json.loads(done.stdout.strip().splitlines()[-1])
    assert done.returncode == 3 and said["founded"] is False and said["needs"] == "device" and "POOLHOUSE_SIGNING_PASSPHRASE" in said["say"] and "phone" not in said["say"].lower(), done
    assert governance(workspace_node)["mode"] == "unfounded" and workspace_node.client.call("person_keys")["keys"] == []

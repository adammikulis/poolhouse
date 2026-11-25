"""`ph.pool.found`, `ph.pool.status` and `ph.pool.join` as an agent calls them: a node of the test's own, a key this computer makes, or a key another computer holds that the test made.

Nobody is asked and nothing waits. The sentences are what an agent passes on to its owner, so they are checked for what they name and for their length.
"""

from __future__ import annotations

import pytest
from node_kit import NoKeystore, enrol_as_phone_key

import poolhouse as ph
from poolhouse import keystore
from poolhouse.board import founding
from poolhouse.fleet.onboard.manifest import Signer

pytest_plugins = ["node_kit"]


def test_status_of_a_fresh_device_says_it_is_unfounded_and_what_to_do(workspace_node):
    pool = ph.pool.status()
    assert pool.governance == "unfounded" and pool.name == "" and "pool of one" in pool.missing and len(pool.members) == 1


def test_found_makes_the_pool_at_once_with_a_key_this_computer_makes_and_a_second_call_says_it_is_there(workspace_node):
    made = ph.pool.found("home")
    assert isinstance(made, ph.pool.Founding) and not made.already and made.pool.name == "home" and made.pool.governance == "governed"
    assert "now in the pool home" in made.say and "phone" not in made.say.lower() and "terminal" not in made.say.lower() and len(made.say) <= 260
    assert len(workspace_node.client.call("person_keys")["keys"]) == 1, "the node enrolled the key it made"
    assert (workspace_node.state / "person" / "pools").is_dir(), "and this computer kept it"
    again = ph.pool.found("other")
    assert again.already and "already in the pool home" in again.say and again.pool.id == made.pool.id


def test_found_says_in_one_sentence_what_is_missing_when_this_computer_cannot_keep_the_key(workspace_node, monkeypatch):
    monkeypatch.setattr(keystore, "default", NoKeystore)  # a machine with no keystore of its own, and no passphrase to keep a key file under
    monkeypatch.delenv("POOLHOUSE_SIGNING_PASSPHRASE")
    with pytest.raises(ph.pool.Refusal) as refused:
        ph.pool.found("home")
    assert isinstance(refused.value, ph.Denied) and "POOLHOUSE_SIGNING_PASSPHRASE" in str(refused.value) and "phone" not in str(refused.value).lower()
    assert refused.value.needs == "device" and refused.value.then == "stop" and len(str(refused.value)) <= 260
    assert ph.pool.status().governance == "unfounded"
    assert workspace_node.client.call("person_keys")["keys"] == [], "a refusal made no key"
    assert not (workspace_node.state / "person" / "pools").exists(), "and kept none"


def test_found_with_another_computers_key_as_admin_makes_the_pool_at_once_and_a_second_call_says_it_is_there(workspace_node):
    admin = founding.Admin(Signer.generate())
    enrol_as_phone_key(workspace_node, admin.key)
    made = ph.pool.found("home", "open", admin=admin)
    assert isinstance(made, ph.pool.Founding)
    assert not made.already and made.pool.name == "home" and made.pool.policy == "open"
    assert made.pool.governance == "governed" and "now in the pool home" in made.say
    again = ph.pool.found("other", admin=admin)
    assert again.already and "already in the pool home" in again.say and again.pool.id == made.pool.id
    assert ph.pool.status().missing == "", "a governed pool with a strong key needs nothing from its person"


def test_join_without_a_code_or_a_fingerprint_says_what_is_needed(workspace_node):
    with pytest.raises(ph.Error, match=r"code|fingerprint"):
        ph.pool.join("192.168.1.9")


def test_a_change_waiting_for_signers_does_not_ask_for_a_phone():
    from poolhouse.api.pool import _missing

    waiting = [{"act": "a", "have": 1, "need": 2, "why": "it needs 2 signers and has 1"}]
    said = _missing({"mode": "governed", "waiting_for_signers": waiting})
    assert "phone" not in said and "approvals from the keys that govern it" in said, said
    assert _missing({"mode": "governed", "waiting_for_signers": []}) == ""

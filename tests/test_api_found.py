"""`ph.pool.found`, `ph.pool.status` and `ph.pool.join` as an agent calls them: a node of the test's own, the person's key made by the test, the phone's tap signed by a key the test made.

The sentences are what an agent passes on to its owner, so they are checked for what they name and for their length.
"""

from __future__ import annotations

import threading
import time

import pytest
from node_kit import enrol_as_phone_key
from phone_kit import add_phone, endpoint_of

import poolhouse as ph
from poolhouse.board import founding
from poolhouse.fleet.onboard.manifest import Signer

pytest_plugins = ["node_kit", "phone_kit"]


def tap(node, phone, how: str = "approve", number: list[str] | None = None) -> str:
    """The person's phone answers the one request that waits for it."""
    endpoint = endpoint_of(node)
    for _ in range(150):
        waiting = endpoint.ok("list", {}, token=phone.token)["approvals"]
        if waiting:
            seen = endpoint.ok("get", {"id": waiting[0]["id"]}, token=phone.token)
            while how == "approve" and not number:
                time.sleep(0.05)
            return str(endpoint.ok("decide", phone.decision(seen, how, pick=(number or [""])[0]))["state"])
        time.sleep(0.1)
    raise AssertionError("the agent never asked the phone")


def test_status_of_a_fresh_device_says_it_is_unfounded_and_what_to_do(workspace_node):
    pool = ph.pool.status()
    assert pool.governance == "unfounded" and pool.name == "" and "pool of one" in pool.missing and len(pool.members) == 1


def test_found_on_a_computer_with_no_prompt_puts_the_question_on_a_terminal_and_never_asks_for_a_phone(workspace_node):
    with pytest.raises(ph.pool.Refusal) as refused:
        ph.pool.found("home")
    assert isinstance(refused.value, ph.Denied) and "terminal" in str(refused.value) and "phone" not in str(refused.value).lower()
    assert refused.value.needs == "approval" and len(str(refused.value)) <= 260 and ph.pool.status().governance == "unfounded"
    assert workspace_node.client.call("person_keys")["keys"] == [], "a refusal made no key"
    assert not (workspace_node.state / "person" / "pools").exists(), "and kept none"


def test_found_with_a_linked_phone_asks_the_phone_and_a_second_call_says_it_is_there(phone_node):
    phone = add_phone(phone_node)
    admin = founding.Admin(Signer.generate())
    enrol_as_phone_key(phone_node, admin.key)
    told: list[str] = []
    done: dict = {}
    thread = threading.Thread(target=lambda: done.update(made=ph.pool.found("home", "open", admin=admin, say=told.append)))
    thread.start()
    answered: list[str] = []
    number: list[str] = []
    asker = threading.Thread(target=lambda: answered.append(tap(phone_node, phone, number=number)))
    asker.start()
    while not told:
        time.sleep(0.05)
    number.append(told[0].rsplit(" ", 1)[1].rstrip("."))
    asker.join(30)
    thread.join(30)
    made = done["made"]
    assert isinstance(made, ph.pool.Founding)
    assert answered == ["approved"] and not made.already and made.pool.name == "home" and made.pool.policy == "open"
    assert made.pool.governance == "governed" and "now in the pool home" in made.say
    again = ph.pool.found("other", admin=admin)
    assert again.already and "already in the pool home" in again.say and again.pool.id == made.pool.id
    assert ph.pool.status().missing == "", "a governed pool with a strong key needs nothing from its person"


def test_a_linked_phone_is_not_asked_for_the_key_this_computer_makes(phone_node):
    add_phone(phone_node)
    with pytest.raises(ph.pool.Refusal) as refused:
        ph.pool.found("home")
    assert "terminal" in str(refused.value) and "phone" not in refused.value.where
    assert ph.pool.status().governance == "unfounded"


def test_join_without_a_code_or_a_fingerprint_says_what_is_needed(workspace_node):
    with pytest.raises(ph.Error, match=r"code|fingerprint"):
        ph.pool.join("192.168.1.9")


def test_a_change_waiting_for_signers_does_not_ask_for_a_phone():
    from poolhouse.api.pool import _missing

    waiting = [{"act": "a", "have": 1, "need": 2, "why": "it needs 2 signers and has 1"}]
    said = _missing({"mode": "governed", "waiting_for_signers": waiting})
    assert "phone" not in said and "approvals from the keys that govern it" in said, said
    assert _missing({"mode": "governed", "waiting_for_signers": []}) == ""

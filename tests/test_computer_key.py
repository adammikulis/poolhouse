"""The computer's own key for a pool: it signs only what the person said yes to at the computer's prompt, and a pool is founded with it with no phone.

The node is the one as shipped (it asks the person at a terminal it reads itself, `--presence terminal`): that prompt is of the kind the
installed software raises where it has no operating system dialog, so a key it enrols counts as a typed yes and founds nothing. The success
path therefore plants the key's claim the way `wire.plant_key` writes it ("planted, not custody"), and the card is answered at that terminal.
What these tests check is the ticket: no signature without one, none for other bytes, none twice, and no prompt for a process an agent started.
"""

from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager

import pytest
from pool_check import wire
from pool_check.terminal import at_terminal

from poolhouse.board import computer_key, founding, presence, session as board_session
from poolhouse.board.founding import Admin, Refusal

pytest_plugins = ["node_kit"]


class MemoryKeystore:
    """A keystore for a test: a key that wraps what it is given and opens it only for the account it wrapped it for."""

    def __init__(self) -> None:
        self.master = hashlib.sha256(b"a test keystore").digest()

    def available(self) -> bool:
        return True

    def wrap(self, purpose: str, account: str, raw: bytes) -> bytes:
        pad = hashlib.sha256(self.master + purpose.encode() + account.encode()).digest()
        return bytes(a ^ b for a, b in zip(raw, pad, strict=False))

    def unwrap(self, purpose: str, account: str, blob: bytes) -> bytes:
        return self.wrap(purpose, account, blob)


def person_session(node) -> board_session.Session:
    """The person's own session on the pool board, as `poolhouse-pool found` registers it."""
    got = node.client.call("register_person", "pool", "", session="the-person")
    return board_session.Session(node.client, "pool", got["token"], got["name"])


def agent_session(node) -> board_session.Session:
    made = board_session.register(node.client, "pool", board_session.Native("claude-sonnet-5-5", "claude-code", "pool-agent"), "")
    return board_session.Session(node.client, "pool", made.token, made.name)


def make_key(node, session) -> computer_key.ComputerKey:
    return computer_key.PoolKeys(node.state, node.client, session.token, ks=MemoryKeystore()).make()


@contextmanager
def person_at_the_terminal(answer: str = "yes"):
    """A terminal the node writes its question on, and a person who types ``answer`` there when it appears."""
    terminal = at_terminal()
    typed = threading.Thread(target=lambda: terminal.shows("Type yes", 40.0) and terminal.types(answer + terminal.ENTER), daemon=True)
    typed.start()
    try:
        yield presence.Voice(terminal=terminal.name)
    finally:
        typed.join(5)
        terminal.close()


def plant(node, key: computer_key.ComputerKey) -> None:
    """Record the key's claim in the node's person keys the way an enrolment at the operating system's prompt does, and start the node again."""
    node.stop()
    wire.plant_key(node.state, key.public.hex(), "computer")
    node.start()


def draft_of(node, session, key, name="home") -> dict:
    keys = [{"key": key.public.hex(), "person": "owner", "label": "this computer's key"}]
    return node.client.call("pool_draft_found", "", session.token, title=name, policy="secure", project="", keys=keys)


def test_signing_without_a_ticket_raises(workspace_node):
    session = person_session(workspace_node)
    key = make_key(workspace_node, session)
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(b"anything")


def test_a_ticket_for_other_bytes_is_refused_and_each_payload_is_signed_once(workspace_node):
    session = person_session(workspace_node)
    key = make_key(workspace_node, session)
    plant(workspace_node, key)
    session = person_session(workspace_node)
    key.use(session.token)
    draft = draft_of(workspace_node, session, key)
    payload = bytes.fromhex(draft["accept"][0]["sign_bytes"])
    with person_at_the_terminal() as voice:
        key.approve(draft["card"], enrolled=True, voice=voice)
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(payload + b"x")
    assert len(key.sign_bytes(payload)) == 64
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(payload)


def test_a_pool_is_founded_with_the_computers_key_and_no_phone(workspace_node):
    session = person_session(workspace_node)
    key = make_key(workspace_node, session)
    plant(workspace_node, key)
    session = person_session(workspace_node)
    key.use(session.token)
    assert workspace_node.client.call("pool_status")["governance"]["mode"] == "unfounded"
    with person_at_the_terminal() as voice:
        made = founding.found(session, Admin(key), "home", "secure", founding.Terms(voice=voice))
    assert (made.name, made.policy, made.epoch, made.already) == ("home", "secure", 1, False)
    gov = workspace_node.client.call("pool_status")["governance"]
    assert gov["mode"] == "governed" and gov["admins"][0]["claim"]["method"] == "keystore-prompt" and gov["admins"][0]["counted"] == "computer"
    assert gov["floors"] == {"routine": "computer", "sensitive": "computer", "governance": "computer"}
    assert not any(k["claim"]["method"] == "phone-keystore" for k in gov["admins"]), "no phone is anywhere in this pool"


def test_a_no_at_the_prompt_founds_nothing(workspace_node):
    session = person_session(workspace_node)
    key = make_key(workspace_node, session)
    plant(workspace_node, key)
    session = person_session(workspace_node)
    key.use(session.token)
    with person_at_the_terminal("no") as voice, pytest.raises(Refusal) as refused:
        founding.found(session, Admin(key), "home", "secure", founding.Terms(voice=voice))
    assert refused.value.then == "stop" and refused.value.needs == "approval" and len(refused.value.say) <= 260
    assert workspace_node.client.call("pool_status")["governance"]["mode"] == "unfounded"
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(b"x")


def test_a_key_a_typed_yes_made_founds_nothing_and_says_how_to_fix_it(workspace_node):
    session = person_session(workspace_node)
    key = make_key(workspace_node, session)
    with pytest.raises(Refusal) as refused:
        founding.found(session, Admin(key), "home", "secure")
    assert "prompt" in refused.value.say and refused.value.needs == "device"
    assert workspace_node.client.call("pool_status")["governance"]["mode"] == "unfounded"


def test_a_process_an_agent_started_gets_no_key_and_raises_no_prompt(workspace_node, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    session = agent_session(workspace_node)
    with pytest.raises(Refusal) as refused:
        founding.default_admin(session)
    assert "terminal of your own" in refused.value.say and refused.value.then == "ask-owner"
    assert not (workspace_node.state / "person" / "pools").exists(), "no key was made"


def test_an_agent_session_cannot_get_the_prompt_raised_at_the_node(workspace_node):
    session = person_session(workspace_node)
    key = make_key(workspace_node, session)
    agent = agent_session(workspace_node)
    draft = draft_of(workspace_node, agent, key)
    with pytest.raises(Exception, match="person's own session"):
        workspace_node.client.call("presence_ask", "", agent.token, purpose="pool_key", public=key.public.hex(), card=draft["card"], terminal="")


def test_a_ticket_the_node_never_made_signs_nothing(workspace_node):
    session = person_session(workspace_node)
    key = make_key(workspace_node, session)
    key.ticket = "a guess"
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(b"x")

"""The computer's own key for a pool: the node makes it for the card, the person's yes at the prompt enrols it and hands it to the person's session
once, and it signs only what the person said yes to there. A pool is founded with it with no phone.

The node is the one as shipped for a computer with no prompt of its own (it asks the person at a terminal it reads itself, `--presence terminal`): so
the pool says it is approved by a typed yes, and the card is answered at that terminal. What these tests check is the person's own session
(proven by the secret the node keeps for the person), the key the node made, and the ticket: no signature without one, none for other bytes,
none twice, and no card or key for a session an agent started.
"""

from __future__ import annotations

import hashlib
import socket
import threading
from contextlib import contextmanager

import pytest
from pool_check import wire
from pool_check.terminal import at_terminal

import poolhouse as ph
from poolhouse import keystore, node_launch
from poolhouse.api import _node
from poolhouse.board import (
    client as board_client,
    computer_key,
    founding,
    presence,
    session as board_session,
)
from poolhouse.board.founding import Refusal

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


KS = MemoryKeystore()


def secret_of(node) -> str:
    return (node.state / "person" / "ui.secret").read_text().strip()


def person_session(node) -> board_session.Session:
    """The person's own session on the pool board, registered with the secret the node keeps for the person."""
    got = node.client.call("register_person", "pool", "", session="the-person", proof=secret_of(node))
    return board_session.Session(node.client, "pool", got["token"], got["name"])


def agent_session(node) -> board_session.Session:
    made = board_session.register(node.client, "pool", board_session.Native("claude-sonnet-5-5", "claude-code", "pool-agent"), "")
    return board_session.Session(node.client, "pool", made.token, made.name)


@contextmanager
def person_at_the_terminal(*answers: str, asked: list[str] | None = None):
    """A terminal the node writes its questions on, and a person who types each of ``answers`` there when a question appears (each is added to
    ``asked`` as it is typed)."""
    terminal = at_terminal()
    asked = [] if asked is None else asked

    def typing() -> None:
        for answer in answers or ("yes",):
            if not terminal.shows("Type yes", 40.0):
                return
            asked.append(answer)
            terminal.types(answer + terminal.ENTER)

    typed = threading.Thread(target=typing, daemon=True)
    typed.start()
    try:
        yield presence.Voice(terminal=terminal.name)
    finally:
        typed.join(5)
        terminal.close()


def founded(node, *answers: str) -> tuple[board_session.Session, computer_key.ComputerKey]:
    """Found the pool "home" as the person at the terminal; the person's session and the key the node made for it."""
    session = person_session(node)
    with person_at_the_terminal(*(answers or ("yes",))) as voice:
        founding.found_on_computer(session, "home", "secure", voice, ks=KS)
    listed = {str(k["fingerprint"]) for k in node.client.call("pool_status")["governance"]["admins"]}
    key = computer_key.PoolKeys(node.state, node.client, session.token, ks=KS).find(listed)
    assert key is not None
    return session, key.use(session.token)


def test_signing_without_a_ticket_raises(workspace_node):
    session, key = founded(workspace_node)
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(b"anything")
    assert session.token


def test_a_ticket_for_other_bytes_is_refused_and_each_payload_is_signed_once(workspace_node):
    session, key = founded(workspace_node)
    draft = workspace_node.client.call("pool_draft", "", session.token, kind="set_holds", body={"admin_add_h": 48})
    payload = bytes.fromhex(draft["sign_bytes"])
    with person_at_the_terminal() as voice:
        key.approve(draft["card"], voice=voice)
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(payload + b"x")
    assert len(key.sign_bytes(payload)) == 64
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(payload)


def test_a_pool_is_founded_with_a_key_this_computer_makes_and_no_phone(workspace_node):
    assert workspace_node.client.call("pool_status")["governance"]["mode"] == "unfounded"
    session, key = founded(workspace_node)
    gov = workspace_node.client.call("pool_status")["governance"]
    assert (gov["name"], gov["mode"]) == ("home", "governed")
    assert [a["claim"]["method"] for a in gov["admins"]] == ["keystore-typed"], "a computer with no prompt of its own makes a typed-yes key"
    assert gov["floors"] == {"routine": "weak", "sensitive": "weak", "governance": "weak"} and gov["delegates"] == []
    assert "typed yes" in gov["approved"] and "program running as you" in gov["approved"]
    assert not any(k["claim"]["method"] == "phone-keystore" for k in gov["admins"]), "no phone is anywhere in this pool"
    assert [a["fingerprint"] for a in gov["admins"]] == [hashlib.sha256(key.public).hexdigest()], "the pool's one key is the one the node made, kept by this session"
    assert session.name


def test_a_no_at_the_prompt_founds_nothing_and_keeps_no_key(workspace_node):
    session = person_session(workspace_node)
    with person_at_the_terminal("no") as voice, pytest.raises(Refusal) as refused:
        founding.found_on_computer(session, "home", "secure", voice, ks=KS)
    assert refused.value.then == "stop" and refused.value.needs == "approval" and len(refused.value.say) <= 260
    assert workspace_node.client.call("pool_status")["governance"]["mode"] == "unfounded"
    assert not (workspace_node.state / "person" / "pools").exists(), "a no left no key behind"
    assert workspace_node.client.call("person_keys")["keys"] == [], "and enrolled none"


def test_a_session_that_does_not_hold_the_persons_secret_is_not_the_persons_session(workspace_node):
    for proof in ("", "a" * 64, "0" * 63):
        with pytest.raises(board_client.Denied):
            workspace_node.client.call("register_person", "pool", "", session="forged", proof=proof)
    with pytest.raises(board_client.Denied):
        workspace_node.client.call("register_person", "pool", "", session="forged")
    assert (workspace_node.state / "person" / "ui.secret").stat().st_mode & 0o077 == 0, "only the account reads it"


def test_an_agent_session_is_given_no_key_no_card_and_no_prompt(workspace_node, monkeypatch):
    agent = agent_session(workspace_node)
    with pytest.raises(board_client.Denied, match="person's own session"):
        workspace_node.client.call("pool_draft_found", "", agent.token, title="home", policy="secure", project="",
                                   keys=[{"computer": True, "person": "owner", "label": "a key"}])
    held = [{"key": "5866666666666666666666666666666666666666666666666666666666666666", "person": "owner", "label": "a key"}]
    draft = workspace_node.client.call("pool_draft_found", "", agent.token, title="home", policy="secure", project="", keys=held)
    assert draft["card"] is None and "person's own session" in draft["card_why"]
    with pytest.raises(board_client.Denied, match="person's own session"):
        workspace_node.client.call("presence_ask", "", agent.token, purpose="pool_key", card="a" * 64, terminal="")
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(Refusal) as refused:
        founding.found_here(workspace_node.client, "home", "secure")
    assert "terminal of your own" in refused.value.say and refused.value.then == "ask-owner"
    assert not (workspace_node.state / "person" / "pools").exists(), "no key was made"


def test_a_ticket_the_node_never_made_signs_nothing(workspace_node):
    session, key = founded(workspace_node)
    key.ticket = "a guess"
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(b"x")
    assert session.token


@pytest.fixture
def mine(workspace_node, monkeypatch):
    """The test's node is this device's node for `ph.pool`, and the account's keystore is the test's."""
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    monkeypatch.setattr(keystore, "default", lambda: KS)
    return workspace_node


def listening(node) -> int:
    """Start the node again with its network on at a port of this machine's loopback only; the port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    node.stop()
    node_launch.ensure_node(node.state, extra=["--listen", f"127.0.0.1:{port}", *wire.PRESENCE])
    return port


@contextmanager
def typing(monkeypatch, *answers: str, asked: list[str] | None = None):
    """A person at a terminal of their own, which `ph.pool` finds as this process's terminal, who types each of ``answers`` where a question appears."""
    with person_at_the_terminal(*answers, asked=asked) as voice:
        monkeypatch.setattr(presence, "terminal_name", lambda: voice.terminal)
        yield voice


def test_the_persons_own_process_is_the_persons_session_and_a_process_an_agent_started_is_not(mine, monkeypatch):
    person = _node.person_session()
    assert person is not None, "a process that reads the secret the node keeps for the person is the person's"
    kept = mine.client.call("pool_draft_found", "", person.token, title="home", policy="secure", project="", keys=[{"computer": True, "person": "owner", "label": "a key"}])
    assert kept["card"], "and the node lets that session raise a card for a key of this computer"
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _node.person_session() is None, "a process an agent started is never the person's, whatever it can read"
    monkeypatch.delenv("CLAUDECODE")
    secret = mine.state / "person" / "ui.secret"
    away = secret.with_suffix(".away")
    secret.rename(away)
    try:
        assert _node.person_session() is None, "a process that cannot read the secret is not the person's"
    finally:
        away.rename(secret)
    assert _node.person_session() is not None


def test_a_window_is_opened_with_the_persons_yes_even_in_a_pool_a_typed_yes_approved(mine, monkeypatch):
    founded(mine)
    listening(mine)
    asked: list[str] = []
    with typing(monkeypatch, asked=asked):
        opened = ph.pool.add_device()
    assert asked == ["yes"] and len(str(opened["code"])) >= 6 and opened["expires_in_s"] == 300, "the one yes opened the window: a typed-yes pool has no delegate to open it without one"
    assert mine.client.call("pool_status")["governance"]["delegates"] == []


def test_a_no_opens_no_window_and_a_process_an_agent_started_asks_the_person_nothing(mine, monkeypatch):
    founded(mine)
    listening(mine)
    asked: list[str] = []
    with typing(monkeypatch, "no", asked=asked), pytest.raises(ph.pool.Refusal) as refused:
        ph.pool.add_device()
    assert (asked, refused.value.needs, refused.value.then) == (["no"], "approval", "stop")
    monkeypatch.setenv("CLAUDECODE", "1")
    terminal = at_terminal()
    try:
        monkeypatch.setattr(presence, "terminal_name", lambda: terminal.name)
        with pytest.raises(ph.Error):
            ph.pool.add_device()
        assert not terminal.shows("Type yes", 1.0), "an agent's call put no question to the person"
    finally:
        terminal.close()


def test_joining_is_the_persons_yes_for_that_host_and_code_and_a_no_joins_nothing(mine, monkeypatch):
    listening(mine)
    asked: list[str] = []
    with typing(monkeypatch, asked=asked), pytest.raises(ph.pool.Refusal) as unreached:
        ph.pool.join("127.0.0.1", "123456", port=1)
    assert asked == ["yes"] and unreached.value.then == "ask-owner", "the yes was given and the node then went to the address: there is no pool there"
    with typing(monkeypatch, "no", asked=asked), pytest.raises(ph.pool.Refusal) as refused:
        ph.pool.join("127.0.0.1", "123456", port=1)
    assert (asked, refused.value.needs, refused.value.then) == (["yes", "no"], "approval", "stop")
    assert [m.this_device for m in ph.pool.members()] == [True]


def test_a_change_to_who_governs_is_made_with_the_persons_yes_at_the_prompt_and_by_this_computers_key(mine, monkeypatch):
    founded(mine)
    asked: list[str] = []
    with typing(monkeypatch, asked=asked):
        renewed = ph.pool.renew()
    assert asked == ["yes"] and renewed.this_device, "the person was asked for exactly that change, and the node took the act this computer's key signed under it"

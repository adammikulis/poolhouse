"""This computer's own key for a pool: the node makes it for the card, enrols it and hands it to the asking session once (nobody is asked: the node as
shipped runs `--presence auto`), and it signs only under a ticket the node made for exactly those bytes: no signature without one, none for other
bytes, none twice. A pool is founded with it, and changed with it, at once and with no phone.

What these tests check is who the node makes the key and the ticket for (the device's own session, proven by the secret the node keeps for it, and a
live main session; never a subagent), what the key is worth (a key kept under the keystore counts, one kept in a file under a passphrase is only
a typed-yes key), and that a computer that cannot keep the key founds nothing before the node makes one.
"""

from __future__ import annotations

import hashlib
import socket

import pytest
from node_kit import PASSPHRASE, NoKeystore
from pool_check import wire

import poolhouse as ph
from poolhouse import keystore, node_launch
from poolhouse.api import _node
from poolhouse.board import (
    client as board_client,
    computer_key,
    founding,
    session as board_session,
)
from poolhouse.board.founding import Refusal
from poolhouse.fleet.onboard.signing import UNLOCK_ENV

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
    """The device's own session on the pool board, registered with the secret the node keeps for it."""
    got = node.client.call("register_person", "pool", "", session="the-person", proof=secret_of(node))
    return board_session.Session(node.client, "pool", got["token"], got["name"])


def agent_session(node, native: str = "pool-agent", parent: board_session.Session | None = None) -> board_session.Session:
    """A session an agent started: its own token on the pool board (a subagent of ``parent`` when one is named), and no secret of the device's."""
    made = board_session.register(node.client, "pool", board_session.Native("claude-sonnet-5-5", "claude-code", native), parent.token if parent else "")
    return board_session.Session(node.client, "pool", made.token, made.name)


def found_with(node, ks) -> tuple[board_session.Session, computer_key.ComputerKey]:
    """Found the pool "home" as the device's own session, its key kept under ``ks``; the session and the key the node made for it."""
    session = person_session(node)
    founding.found_on_computer(session, "home", "secure", ks=ks)
    listed = {str(k["fingerprint"]) for k in node.client.call("pool_status")["governance"]["admins"]}
    key = computer_key.PoolKeys(node.state, node.client, session.token, ks=ks).find(listed)
    assert key is not None, "the key the node made for the pool is kept here"
    return session, key.use(session.token)


def founded(node) -> tuple[board_session.Session, computer_key.ComputerKey]:
    return found_with(node, KS)


def test_signing_without_a_ticket_raises(workspace_node):
    session, key = founded(workspace_node)
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(b"anything")
    assert session.token


def test_a_ticket_for_other_bytes_is_refused_and_each_payload_is_signed_once(workspace_node):
    session, key = founded(workspace_node)
    draft = workspace_node.client.call("pool_draft", "", session.token, kind="set_holds", body={"admin_add_h": 48})
    payload = bytes.fromhex(draft["sign_bytes"])
    key.approve(draft["card"])
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(payload + b"x")
    assert len(key.sign_bytes(payload)) == 64
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(payload)


def test_a_ticket_the_node_never_made_signs_nothing(workspace_node):
    session, key = founded(workspace_node)
    key.ticket = "a guess"
    with pytest.raises(computer_key.TicketRequired):
        key.sign_bytes(b"x")
    assert session.token


def test_a_pool_is_founded_at_once_with_a_key_this_computer_makes_and_no_phone(workspace_node):
    assert workspace_node.client.call("pool_status")["governance"]["mode"] == "unfounded"
    session, key = founded(workspace_node)
    gov = workspace_node.client.call("pool_status")["governance"]
    assert (gov["name"], gov["mode"]) == ("home", "governed")
    assert [a["claim"]["method"] for a in gov["admins"]] == ["keystore-prompt"], "a key kept under the computer's keystore counts as the computer's own"
    assert not any(k["claim"]["method"] == "phone-keystore" for k in gov["admins"]), "no phone is anywhere in this pool"
    assert [a["fingerprint"] for a in gov["admins"]] == [hashlib.sha256(key.public).hexdigest()], "the pool's one key is the one the node made, kept by this session"
    assert gov["floors"]["sensitive"] != "weak" and [d["fingerprint"] if isinstance(d, dict) else d for d in gov["delegates"]], "a counted key is the pool's approval, and this computer may admit devices"
    assert workspace_node.client.call("pool_status")["waiting"] == [], "nothing waits for anybody"
    assert session.name


def test_a_key_kept_in_a_file_under_a_passphrase_is_a_typed_yes_key_and_signs_after_the_pool_is_founded(workspace_node, monkeypatch):
    monkeypatch.setenv(UNLOCK_ENV, PASSPHRASE)
    session, key = found_with(workspace_node, NoKeystore())
    gov = workspace_node.client.call("pool_status")["governance"]
    assert [a["claim"]["method"] for a in gov["admins"]] == ["file"], "a file under a passphrase counts for less than a key under the computer's keystore"
    assert gov["floors"] == {"routine": "weak", "sensitive": "weak", "governance": "weak"} and gov["delegates"] == [], "and the pool says it is approved by a typed yes"
    assert "typed yes" in gov["approved"]
    draft = workspace_node.client.call("pool_draft", "", session.token, kind="set_holds", body={"admin_add_h": 48})
    key.approve(draft["card"])
    assert len(key.sign_bytes(bytes.fromhex(draft["sign_bytes"]))) == 64, "the key file opens under the passphrase of the environment, never a prompt"


def test_a_computer_that_cannot_keep_the_key_founds_nothing_and_the_node_makes_no_key(workspace_node, monkeypatch):
    monkeypatch.delenv(UNLOCK_ENV, raising=False)
    session = person_session(workspace_node)
    with pytest.raises(Refusal) as refused:
        founding.found_on_computer(session, "home", "secure", ks=NoKeystore())
    assert UNLOCK_ENV in refused.value.say and refused.value.needs == "device" and refused.value.then == "stop"
    assert workspace_node.client.call("pool_status")["governance"]["mode"] == "unfounded"
    assert workspace_node.client.call("person_keys")["keys"] == [], "the node enrolled no key that nobody could keep"
    assert not (workspace_node.state / "person" / "pools").exists()


def test_a_session_that_does_not_hold_the_persons_secret_is_not_the_persons_session(workspace_node):
    for proof in ("", "a" * 64, "0" * 63):
        with pytest.raises(board_client.Denied):
            workspace_node.client.call("register_person", "pool", "", session="forged", proof=proof)
    with pytest.raises(board_client.Denied):
        workspace_node.client.call("register_person", "pool", "", session="forged")
    assert (workspace_node.state / "person" / "ui.secret").stat().st_mode & 0o077 == 0, "only the account reads it"


def test_a_main_session_an_agent_started_is_given_a_card_at_once_and_a_subagent_is_given_none(workspace_node):
    node = workspace_node
    main = agent_session(node)
    sub = agent_session(node, "pool-subagent", parent=main)
    computer = [{"computer": True, "person": "owner", "label": "a key"}]
    held = [{"key": "5866666666666666666666666666666666666666666666666666666666666666", "person": "owner", "label": "a key"}]
    drafted = node.client.call("pool_draft_found", "", main.token, title="home", policy="secure", project="", keys=computer)
    asked = node.client.call("presence_ask", "", main.token, purpose="pool_key", card=drafted["card"], terminal="")
    assert asked["state"] == "confirmed" and asked["presence"] == "auto" and asked["seed"], "a live main session is answered at once, with no terminal"
    with pytest.raises(board_client.Denied, match="person's own session"):
        node.client.call("pool_draft_found", "", sub.token, title="home", policy="secure", project="", keys=computer)
    draft = node.client.call("pool_draft_found", "", sub.token, title="home", policy="secure", project="", keys=held)
    assert draft["card"] is None and "person's own session" in draft["card_why"], "a subagent is given the charter and no card"
    with pytest.raises(board_client.Denied, match="person's own session"):
        node.client.call("presence_ask", "", sub.token, purpose="pool_key", card="a" * 64, terminal="")
    with pytest.raises(board_client.Denied, match="person's own session"):
        node.client.call("presence_ask", "", main.token, purpose="key", public="ab" * 32, terminal="")


def test_a_ticket_is_good_for_the_session_it_was_given_to_alone(workspace_node):
    session, key = founded(workspace_node)
    other = agent_session(workspace_node)
    draft = workspace_node.client.call("pool_draft", "", session.token, kind="set_holds", body={"admin_add_h": 48})
    key.approve(draft["card"])
    payload = bytes.fromhex(draft["sign_bytes"])
    with pytest.raises(computer_key.TicketRequired):
        key.use(other.token).sign_bytes(payload)
    assert len(key.use(session.token).sign_bytes(payload)) == 64, "the refused attempt did not spend the ticket"


@pytest.fixture
def mine(workspace_node, monkeypatch):
    """The test's node is this device's node for `ph.pool`, and the account has no keystore (the passphrase of the environment keeps the key): the pool is
    one a file-held key approves, which this node's own signer may admit devices for without a keystore of its own."""
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    monkeypatch.setattr(keystore, "default", NoKeystore)
    return workspace_node


def founded_here(node) -> tuple[board_session.Session, computer_key.ComputerKey]:
    """The pool "home" founded by the device's own session with the key kept where `ph.pool` finds it: the account's keystore of the test."""
    return found_with(node, None)


def listening(node) -> int:
    """Start the node again with its network on at a port of this machine's loopback only; the port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    node.stop()
    node_launch.ensure_node(node.state, extra=["--listen", f"127.0.0.1:{port}", *wire.PRESENCE])
    return port


def test_the_persons_session_is_proven_by_the_secret_and_not_by_who_started_the_process(mine, monkeypatch):
    person = _node.person_session()
    assert person is not None, "a process that reads the secret the node keeps for the device is the device's own"
    kept = mine.client.call("pool_draft_found", "", person.token, title="home", policy="secure", project="", keys=[{"computer": True, "person": "owner", "label": "a key"}])
    assert kept["card"], "and the node lets that session raise a card for a key of this computer"
    monkeypatch.setenv("CLAUDECODE", "1")
    assert _node.person_session() is not None, "the secret proves the session, whoever started the process: policy, not the session, decides what an agent may have"
    monkeypatch.delenv("CLAUDECODE")
    secret = mine.state / "person" / "ui.secret"
    away = secret.with_suffix(".away")
    secret.rename(away)
    try:
        assert _node.person_session() is None, "a process that cannot read the secret is not the device's own"
    finally:
        away.rename(secret)
    assert _node.person_session() is not None


def test_a_window_is_opened_at_once_with_a_ticket_the_node_makes_for_exactly_that_window(mine):
    founded_here(mine)
    listening(mine)
    opened = ph.pool.add_device()
    assert len(str(opened["code"])) >= 6 and opened["expires_in_s"] == 300
    assert mine.client.call("pool_status")["waiting"] == [], "nothing was put to anybody"
    assert mine.client.call("pool_status")["join_code_open"] is True


def test_joining_is_made_at_once_and_goes_to_the_address_where_there_is_no_pool_to_join(mine):
    listening(mine)
    with pytest.raises(ph.pool.Refusal) as unreached:
        ph.pool.join("127.0.0.1", "123456", port=1)
    assert unreached.value.then == "ask-owner" and unreached.value.needs == "none", "the ticket was made and the node then went to the address: there is no pool there"
    assert [m.this_device for m in ph.pool.members()] == [True]


def test_a_change_to_who_governs_is_signed_at_once_by_this_computers_key_under_a_ticket_for_exactly_that_change(mine):
    founded_here(mine)
    renewed = ph.pool.renew()
    assert renewed.this_device, "the node took the act this computer's key signed"
    assert mine.client.call("pool_status")["waiting"] == [], "nothing was put to anybody"


def test_a_change_is_refused_with_one_sentence_where_no_key_of_this_computer_governs_the_pool(mine):
    founded_here(mine)
    for directory in (mine.state / "person" / "pools").iterdir():
        for name in directory.iterdir():
            name.unlink()
        directory.rmdir()
    with pytest.raises(founding.NoComputerKey) as refused:
        ph.pool.renew()
    assert refused.value.needs == "device" and refused.value.then == "stop"


def test_a_process_that_cannot_read_the_secret_founds_the_pool_as_a_main_session_of_its_own(workspace_node, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(keystore, "default", lambda: KS)
    secret = workspace_node.state / "person" / "ui.secret"
    away = secret.with_suffix(".away")
    secret.rename(away)
    try:
        made = founding.found_here(workspace_node.client, "home", "secure")
    finally:
        away.rename(secret)
    assert (made.name, made.already) == ("home", False)
    gov = workspace_node.client.call("pool_status")["governance"]
    assert gov["mode"] == "governed" and len(gov["admins"]) == 1, "the node asked nobody and made the pool for the main session that asked"
    assert (workspace_node.state / "person" / "pools").is_dir(), "and the key it made is kept here"

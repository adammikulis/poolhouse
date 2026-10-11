"""The first person key and the first phone need the person's yes to a question the node itself asks, which no agent can answer: an agent
that writes the old files, guesses a secret, asks and waits, or starts a node of its own that cannot ask, becomes no key and no phone.

The nodes here are the node as shipped. They ask on a terminal they read themselves (`--presence terminal`), the way a person at a server
would; the operating system's own prompt is raised only by the installed software on the real home (`poolhouse.real_machine`), so no
test raises a dialog.
"""

from __future__ import annotations

import hashlib
import json
import os
import pty
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from node_kit import enrol_first
from pool_check import wire

from poolhouse import node_binary as node_binary_module, node_launch, node_supervise, real_machine
from poolhouse.board import client as board_client, grants, presence, session as board_session
from poolhouse.board.client import Denied
from poolhouse.fleet.onboard.manifest import Signer

pytest_plugins = ["node_kit"]


def register(node) -> str:
    """A session of the demo board that asks as an agent does: its own token, no terminal of the person's."""
    return board_session.register(node.client, node.board, board_session.Native("claude-sonnet-5-5", "claude-code", "agent"), "").token


def held(node) -> list[str]:
    return [k["fingerprint"] for k in node.client.call("person_keys")["keys"]]


@pytest.fixture
def nobody_answers(monkeypatch):
    """A terminal of the test's own that nobody types at, and a short wait for the answer."""
    monkeypatch.setattr(presence, "WAIT_S", 1.0)
    leader, follower = pty.openpty()
    yield os.ttyname(follower)
    os.close(leader)
    os.close(follower)


def test_an_agent_that_asks_and_nobody_answers_becomes_no_person_key(workspace_node, nobody_answers):
    token = register(workspace_node)
    with pytest.raises(Denied, match="Nobody said yes"):
        grants.enrol_first(workspace_node.client, token, Signer.generate(), "an agent", presence.Voice(terminal=nobody_answers))
    assert held(workspace_node) == [], "a key was enrolled without a yes"


def test_a_guessed_secret_and_the_files_an_agent_can_write_enrol_no_first_key(workspace_node):
    token, agent = register(workspace_node), Signer.generate()
    kept = {"sha256": hashlib.sha256(b"guess").hexdigest(), "at_ms": int(time.time() * 1000)}
    for name in ("enroll_secret.json", "phone_offer.json"):
        (workspace_node.state / name).write_text(json.dumps(kept))
    for secret in ("", "guess", kept["sha256"], "a" * 43):
        with pytest.raises(Denied):
            grants.enrol(workspace_node.client, token, {**grants.enrolment(agent, "an agent"), "secret": secret})
    assert held(workspace_node) == []


def test_a_secret_is_the_nodes_own_and_one_wrong_guess_spends_it(workspace_node):
    token, person = register(workspace_node), Signer.generate()
    said = wire.answer_presence(workspace_node.state, token, "key", person.public.hex())["result"]
    assert said["state"] == "confirmed" and said["presence"] == "terminal"
    with pytest.raises(Denied):
        grants.enrol(workspace_node.client, token, {**grants.enrolment(person, "a test person"), "secret": "guess"})
    with pytest.raises(Denied):
        grants.enrol(workspace_node.client, token, {**grants.enrolment(person, "a test person"), "secret": said["secret"]})
    assert held(workspace_node) == [], "the secret was spent by the wrong guess"


def test_the_person_typing_yes_to_the_node_enrols_the_first_key_and_a_second_secret_is_not_made_for_a_second_first_key(workspace_node):
    token, person = register(workspace_node), Signer.generate()
    assert enrol_first(workspace_node.client, token, person)["fingerprint"] == grants.fingerprint(person.public)
    assert held(workspace_node) == [grants.fingerprint(person.public)]
    again = wire.answer_presence(workspace_node.state, token, "key", Signer.generate().public.hex())
    assert not again["ok"] and again["code"] == "denied", "a key is enrolled already: a further one is approved by it, not by this question"


def test_the_persons_yes_is_for_the_key_it_was_asked_about_and_no_other_key(workspace_node):
    token, person, agent = register(workspace_node), Signer.generate(), Signer.generate()
    said = wire.answer_presence(workspace_node.state, token, "key", person.public.hex())["result"]
    with pytest.raises(Denied):
        grants.enrol(workspace_node.client, token, {**grants.enrolment(agent, "an agent"), "secret": said["secret"]})
    assert held(workspace_node) == [], "a yes for one key enrolled another"
    asked = wire.answer_presence(workspace_node.state, token, "key", "")
    assert not asked["ok"] and asked["code"] == "invalid", "a first key is asked for with its key"


def test_a_node_started_with_no_way_to_ask_refuses_the_first_key_and_the_first_phone(node_binary):
    root = Path(tempfile.mkdtemp(prefix="ml", dir="/tmp"))
    state = root / "node"
    state.mkdir(mode=0o700)
    node_supervise.point(state, node_binary, node_binary_module.sha256(node_binary))
    try:
        node_launch.ensure_node(state, extra=[])
        client = board_client.Client(state)
        token = board_session.register(client, "demo", board_session.Native("claude-sonnet-5-5", "claude-code", "x"), "").token
        for purpose in ("key", "phone"):
            with pytest.raises(Denied, match="no way to ask"):
                presence.ask(client, token, purpose, Signer.generate().public.hex() if purpose == "key" else "")
    finally:
        node_launch.stop_node(state)
        shutil.rmtree(root, ignore_errors=True)


def test_the_launcher_asks_with_the_system_prompt_only_for_the_installed_software_on_the_real_home(monkeypatch):
    monkeypatch.setattr(real_machine, "allowed", lambda: False)
    assert node_launch._presence(["--lan", "--presence", "os"]) == ["--lan"], "a test, a checkout or a rig never asks the system"
    assert node_launch._presence(["--presence", "terminal"]) == ["--presence", "terminal"]
    assert node_launch._presence(None) == []
    monkeypatch.setattr(real_machine, "allowed", lambda: True)
    assert node_launch._presence(["--lan"]) == ["--lan", "--presence", "os"]
    assert node_launch._presence(["--presence", "terminal", "--lan"]) == ["--lan", "--presence", "os"], "an agent's own terminal can answer its own question"


def test_no_switch_of_the_shipped_node_answers_for_the_person():
    app = Path(__file__).resolve().parents[1] / "app" / "poolhouse-node"
    assert "[features]" not in (app / "Cargo.toml").read_text(encoding="utf-8"), "a cargo feature that answers the question would ship"
    for source in app.rglob("*.rs"):
        if "tests" in source.parts:
            continue
        text = source.read_text(encoding="utf-8")
        assert "cfg(feature" not in text and "AlwaysYes" not in text, f"{source} compiles a way to answer for the person only in some builds"

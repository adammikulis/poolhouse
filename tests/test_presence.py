"""The first person key is made by the node at once, with nobody asked: the node as shipped (`--presence auto`) hands the
one-time secret to the device's own session as it is asked for. What stays is who may ask and what a secret is: only the device's own session
(registered with the secret the node keeps for it) asks for the first key; a secret is the node's own, kept in its memory,
bound to the key it was made for and spent by one wrong guess; and an agent that writes the old files, guesses a secret or asks as its own
session becomes no key.

Nothing here types, taps or waits: no test raises a dialog or puts a question on a terminal.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
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
    """The device's own session of the demo board, registered with the secret the node keeps for it: the only session that asks for a first key."""
    return board_session.register(node.client, node.board, board_session.Native("person", "poolhouse-ui", "the-person"), "", person=True).token


def agent(node) -> str:
    """A session of the demo board that an agent started: its own token, no secret of the device's."""
    return board_session.register(node.client, node.board, board_session.Native("claude-sonnet-5-5", "claude-code", "agent"), "").token


def held(node) -> list[str]:
    return [k["fingerprint"] for k in node.client.call("person_keys")["keys"]]


def test_the_device_asks_for_the_first_key_and_has_it_at_once_with_no_terminal_and_no_waiting(workspace_node):
    token, key = register(workspace_node), Signer.generate()
    began = time.monotonic()
    said = presence.ask(workspace_node.client, token, key.public.hex())
    assert time.monotonic() - began < 5, "the answer was there as the question was asked"
    assert said["state"] == "confirmed" and said["presence"] == "auto" and isinstance(said["secret"], str) and said["secret"]
    assert held(workspace_node) == [], "the secret is not the key: nothing is enrolled until the key is sent with it"
    grants.enrol(workspace_node.client, token, {**grants.enrolment(key, "a test device"), "secret": said["secret"]})
    assert held(workspace_node) == [grants.fingerprint(key.public)]


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
    said = wire.answer_presence(workspace_node.state, person.public.hex())["result"]
    assert said["state"] == "confirmed" and said["presence"] == "auto"
    with pytest.raises(Denied):
        grants.enrol(workspace_node.client, token, {**grants.enrolment(person, "a test device"), "secret": "guess"})
    with pytest.raises(Denied):
        grants.enrol(workspace_node.client, token, {**grants.enrolment(person, "a test device"), "secret": said["secret"]})
    assert held(workspace_node) == [], "the secret was spent by the wrong guess"


def test_the_first_key_is_enrolled_by_that_ask_and_a_second_secret_is_not_made_for_a_second_first_key(workspace_node):
    token, person = register(workspace_node), Signer.generate()
    assert enrol_first(workspace_node.client, token, person)["fingerprint"] == grants.fingerprint(person.public)
    assert held(workspace_node) == [grants.fingerprint(person.public)]
    again = wire.answer_presence(workspace_node.state, Signer.generate().public.hex())
    assert not again["ok"] and again["code"] == "denied", "a key is enrolled already: a further one is approved by it, not by this ask"


def test_the_secret_is_for_the_key_it_was_asked_about_and_no_other_key(workspace_node):
    token, person, agent = register(workspace_node), Signer.generate(), Signer.generate()
    said = wire.answer_presence(workspace_node.state, person.public.hex())["result"]
    with pytest.raises(Denied):
        grants.enrol(workspace_node.client, token, {**grants.enrolment(agent, "an agent"), "secret": said["secret"]})
    assert held(workspace_node) == [], "a secret made for one key enrolled another"
    asked = wire.answer_presence(workspace_node.state, "")
    assert not asked["ok"] and asked["code"] == "invalid", "a first key is asked for with its key"


def test_a_node_started_with_no_presence_flag_asks_nobody_either(node_binary):
    root = Path(tempfile.mkdtemp(prefix="ml", dir="/tmp"))
    state = root / "node"
    state.mkdir(mode=0o700)
    node_supervise.point(state, node_binary, node_binary_module.sha256(node_binary))
    try:
        node_launch.ensure_node(state, extra=[])
        client = board_client.Client(state)
        token = board_session.register(client, "demo", board_session.Native("person", "poolhouse-ui", "x"), "", person=True).token
        key = Signer.generate()
        assert grants.enrol_first(client, token, key, "a test device")["fingerprint"] == grants.fingerprint(key.public), "the default is to ask nobody, so the first key is made at once"
    finally:
        node_launch.stop_node(state)
        shutil.rmtree(root, ignore_errors=True)


def test_the_launcher_never_starts_a_node_that_waits_for_a_prompt(monkeypatch):
    for real in (False, True):
        monkeypatch.setattr(real_machine, "allowed", lambda real=real: real)
        assert node_launch._presence(["--lan", "--presence", "os"]) == ["--lan"], "the system's prompt waits for a person: it is dropped on the real machine too"
        assert node_launch._presence(["--presence", "os", "--lan"]) == ["--lan"]
        assert node_launch._presence(["--lan"]) == ["--lan"], "no flag adds one: the node's own default asks nobody"
        assert node_launch._presence(None) == []
    assert node_launch._presence(["--presence", "auto"]) == ["--presence", "auto"]


def test_no_build_flag_of_the_shipped_node_changes_who_is_asked():
    app = Path(__file__).resolve().parents[1] / "app" / "poolhouse-node"
    assert "[features]" not in (app / "Cargo.toml").read_text(encoding="utf-8"), "a cargo feature that changes the answer would ship in some builds only"
    for source in app.rglob("*.rs"):
        if "tests" in source.parts:
            continue
        text = source.read_text(encoding="utf-8")
        assert "cfg(feature" not in text and "AlwaysYes" not in text, f"{source} compiles a way to answer only in some builds"


def test_a_session_an_agent_started_cannot_make_the_first_key_and_a_forged_proof_registers_nothing(workspace_node):
    token = agent(workspace_node)
    with pytest.raises(Denied, match="person's own session"):
        presence.ask(workspace_node.client, token, Signer.generate().public.hex())
    assert held(workspace_node) == []
    with pytest.raises(board_client.Denied):
        workspace_node.client.call("register_person", workspace_node.board, "", session="forged", proof="a" * 64)


def test_the_rigs_person_asks_as_the_devices_own_session_and_is_answered_at_once(workspace_node):
    said = wire.answer_presence(workspace_node.state, Signer.generate().public.hex())
    assert said["ok"] and said["result"]["state"] == "confirmed" and said["result"]["presence"] == "auto" and said["result"]["secret"], said
    forged = wire.call(workspace_node.state, "register_person", "pool", "", {"session": "the-rig-person", "proof": "a" * 64})
    assert not forged["ok"] and forged["code"] == "denied", "the rig's person is the node's own person: a wrong secret is nobody"


def test_wire_runs_as_a_script_where_only_the_rigs_own_directory_is_on_the_path():
    """The rig runs `wire.py` by its path on every device (a container has only the rig's directory), so it needs nothing beside it."""
    done = subprocess.run([sys.executable, str(Path(wire.__file__)), "--help"], capture_output=True, text=True, timeout=60, check=False, cwd=Path(wire.__file__).parent)
    assert done.returncode == 0 and "presence" in done.stdout, done.stderr

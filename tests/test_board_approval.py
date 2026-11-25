"""Asking for grants from a shell: decided at once by policy, never left pending. A request is approved when policy allows it and refused with the reason
when it does not, in a pool as outside one, and the bytes a second implementation agrees on are the ones this one signs. Nobody types and nothing waits."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from test_computer_key import KS

from poolhouse import keystore
from poolhouse.board import approval, founding, grants
from poolhouse.board.approval import Approval

pytest_plugins = ["node_kit"]
VECTORS = Path(__file__).parent / "fixtures" / "phone-approval-vectors.json"
LABELS = ("FLAG", "POOL", "RISK", "WHO", "ASK", "SCOPE", "CANNOT", "WHY", "DENY", "EXPIRES")


def test_the_bytes_and_signature_another_implementation_made_are_the_ones_this_one_builds_and_checks():
    file = json.loads(VECTORS.read_text(encoding="utf-8"))
    key = file["key"]["fingerprint"]
    assert grants.approval_bytes(file["challenge"], "approve", key).decode() == file["approve_bytes"]
    assert grants.approval_bytes(file["challenge"], "deny", key).decode() == file["deny_bytes"]
    for decision in ("block", "approve_repeat"):
        built = grants.approval_bytes(file["challenge"], decision, key)
        assert built.decode() == file[f"{decision}_bytes"], decision
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(file["key"]["public"])).verify(
            bytes.fromhex(file[f"{decision}_signature"]), built, ec.ECDSA(hashes.SHA256()))
    assert set(file["challenge"]["context"]) == {"said", "verdict", "taint", "held", "prompted", "repeat", "pick", "block_ms"}
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(file["key"]["public"]))
    public.verify(bytes.fromhex(file["approve_signature"]), file["approve_bytes"].encode(), ec.ECDSA(hashes.SHA256()))
    assert hashlib.sha256(bytes.fromhex(file["key"]["public"])).hexdigest() == key
    assert "ASK 2: Share the board demo with the board lab, read and write." in file["challenge"]["text"]


def test_a_request_reads_back_in_words_from_the_bytes_that_are_signed_and_every_line_is_in_them():
    file = json.loads(VECTORS.read_text(encoding="utf-8"))
    shown = grants.describe(file["approve_bytes"].encode())
    assert shown.startswith("APPROVE the request ap-0123456789abcdef of claude-1a2b3c on board demo, which reads:")
    assert all(line.strip() in shown for line in file["challenge"]["text"].split("\n")), "every line of the request is in the words"
    assert grants.describe(file["deny_bytes"].encode()).startswith("DENY the request")
    assert grants.describe(file["block_bytes"].encode()).startswith("DENY AND BLOCK the request")
    assert grants.describe(file["approve_repeat_bytes"].encode()).startswith("APPROVE AGAIN (it was declined before) the request")


def test_every_state_a_request_ends_in_has_one_sentence_an_agent_can_pass_on_and_none_is_waiting():
    said = {s: Approval("ap-0123456789abcdef", s, "text", "title", "routine", (), "the node said why").message() for s in approval.SAYS}
    assert set(said) == {"approved", "denied", "expired", "cancelled", "failed"}, "no state is a request that waits"
    assert len(set(said.values())) == len(said) and all(m.endswith((".", "the node said why")) for m in said.values())
    assert said["expired"] == "The request ended before it was answered; ask again if it is still needed."
    assert said["failed"] == "It was approved, but it could not be carried out: the node said why"
    assert "phone" not in " ".join(said.values()).lower() and "you" not in " ".join(said.values()).lower().split(), "no sentence is for a person who is not there"
    assert not hasattr(Approval, "wait") and not hasattr(Approval, "cancel"), "a decided request has nothing to wait for or withdraw"


def test_the_shell_asks_and_has_the_answer_at_once_with_nothing_left_to_read_back_or_wait_for(workspace_node):
    team = workspace_node
    lead = team.member("lead")
    done = team.cli("approval", "request", "add_device", "link_write:demo>lab", "--for", "10m", "--uses", "1", "--note", "adding the Linux box", "--json", who=lead)
    made = json.loads(done.stdout)
    assert done.returncode == 0 and made["state"] == "approved" and made["risk"] == "sensitive" and len(made["grants"]) == 2, done
    assert set(made) == {"id", "state", "message", "risk", "grants", "text"}, "a decided request names no handle to wait on and no time it ends"
    for label in LABELS:
        assert re.search(rf"^{label}( \d)?:", made["text"], re.M), f"{label} is missing from what the request says"
    assert team.session(lead).call("approval_pending")["approvals"] == [], "nothing waits"
    assert sorted(g.action for g in grants.listing(team.session(lead)).grants if g.status == "live") == ["add_device", "link_write"]
    refused = team.cli("approval", "request", "grant_issue", who=lead)
    assert refused.returncode == 3 and "grant_issue is a floor" in refused.stderr + refused.stdout
    assert team.cli("approval", "request", "sync_now", "--wait", "0", who=lead).returncode == 2, "there is no wait to ask for: it is answered"
    for verb in ("status", "wait", "cancel", "list", "pending", "decide", "blocks", "lift"):
        assert team.cli("approval", verb, who=lead).returncode == 2, f"`approval {verb}` is gone: a request is decided when it is made"


def test_in_a_pool_a_grant_that_reaches_beyond_the_device_is_refused_at_once_and_nothing_is_left_pending(workspace_node, monkeypatch):
    monkeypatch.setattr(keystore, "default", lambda: KS)
    team = workspace_node
    lead = team.member("lead")
    founding.found_here(team.client, "home", "secure")
    assert grants.listing(team.session(lead)).governance == "governed"
    for action in ("note_verify", "link_write:demo>lab"):
        done = team.cli("approval", "request", action, "--json", who=lead, timeout=120)
        out = done.stdout + done.stderr
        assert done.returncode == 3 and grants.POOL_GRANTS in json.loads(done.stdout)["error"], out
        assert "phone" not in out.lower() and "waiting for the person" not in out, "no sentence sends anyone to a phone or says it waits"
        assert team.session(lead).call("approval_pending")["approvals"] == [], f"{action}: no request was made to wait on"
    assert [g for g in grants.listing(team.session(lead)).grants if g.status == "live"] == []


def test_in_a_pool_a_grant_the_device_key_could_give_but_cannot_be_made_ready_for_is_refused_at_once_and_nothing_is_left_pending(workspace_node, monkeypatch):
    monkeypatch.setattr(keystore, "default", lambda: KS)
    team = workspace_node
    lead = team.member("lead")
    founding.found_here(team.client, "home", "secure")
    done = team.cli("approval", "request", "link_read:a>b", "--json", who=lead, timeout=120)
    assert done.returncode == 3, done.stdout + done.stderr
    assert team.session(lead).call("approval_pending")["approvals"] == [], "the key could not be made ready, so no request was left to wait on"
    again = team.cli("approval", "request", "sync_now", "--json", who=lead, timeout=120)
    assert "already have a request waiting" not in again.stdout + again.stderr, "the refused request blocked nothing after it"

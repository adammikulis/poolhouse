"""A pool is founded, this device's key is made and grants are issued with nothing asked of anyone: no phone, no typed yes, no terminal.

The node is the one as shipped (`--presence auto`). A process an agent started (CLAUDECODE set) runs the commands; policy decides at once: the
grants an agent may have are signed with the key this device made on first use, and the few it may not have are refused with the reason
before any request exists, so nothing is left pending. Every decision is on the audit log with who asked.
"""

from __future__ import annotations

import json

import pytest
from node_kit import PASSPHRASE
from test_computer_key import KS

from poolhouse import caller, keystore
from poolhouse.board import founding, grants as board_grants

pytest_plugins = ["node_kit"]
AGENT = {"CLAUDECODE": "1", "POOLHOUSE_SIGNING_PASSPHRASE": PASSPHRASE}


def run(node, lead, *argv: str):
    return node.cli(*argv, "--json", who=lead, env=AGENT, timeout=120)


def test_an_agent_founds_a_pool_with_no_phone_no_prompt_and_no_terminal(workspace_node, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(keystore, "default", lambda: KS)
    assert caller.marked(), "an agent runs this process"
    assert workspace_node.client.call("pool_status")["governance"]["mode"] == "unfounded"
    made = founding.found_here(workspace_node.client, "home", "secure")
    assert (made.name, made.already) == ("home", False)
    gov = workspace_node.client.call("pool_status")["governance"]
    assert gov["mode"] == "governed" and gov["name"] == "home"
    assert not any(k["claim"]["method"] == "phone-keystore" for k in gov["admins"]), "no phone is anywhere in this pool"
    again = founding.found_here(workspace_node.client, "home", "secure")
    assert again.already, "founding twice changes nothing"


def test_an_agent_gets_the_grants_policy_allows_at_once_signed_by_the_device_key_made_on_first_use(workspace_node):
    node = workspace_node
    lead = node.member("lead")
    assert board_grants.listing(node.session(lead)).persons == [], "no key is enrolled yet"
    done = run(node, lead, "approval", "request", "note_verify")
    assert done.returncode == 0, done.stdout + done.stderr
    shown = json.loads(done.stdout)
    assert shown["state"] == "approved" and shown["grants"], shown
    held = board_grants.listing(node.session(lead))
    assert len(held.persons) == 1, "this device's key was made and enrolled on the way"
    assert [g.action for g in held.grants if g.status == "live"] == ["note_verify"]
    assert any(r["event"] == "approval.approved" and "agent CLAUDECODE" in r["who"] for r in audit(node))


@pytest.mark.parametrize("action", ["member_remove", "admin_manage", "quarantine_release", "land_trust"])
def test_an_agent_is_refused_the_grants_that_end_a_place_in_the_pool_at_once_and_nothing_is_left_pending(workspace_node, action):
    node = workspace_node
    lead = node.member("lead")
    done = run(node, lead, "approval", "request", action)
    out = done.stdout + done.stderr
    assert done.returncode != 0 and "refused to an agent" in out, out
    assert node.session(lead).call("approval_pending")["approvals"] == [], "no request exists to wait on"
    assert [g for g in board_grants.listing(node.session(lead)).grants if g.action == action] == []
    assert any(r["event"] == "approval.refused" and "agent CLAUDECODE" in r["who"] for r in audit(node))


def audit(node) -> list[dict]:
    path = node.state.parent / "authority-audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

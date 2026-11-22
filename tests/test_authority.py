"""The delegable authority registry: its states, the gates that read it and the ones that never do."""

from __future__ import annotations

import argparse
import re

import pytest
from taskboard_kit import board as _board_fixture

from poolhouse import authority, caller, chatpolicy, keystore, node_launch
from poolhouse.board.tasks import Tasks
from poolhouse.caller import AgentRefused
from poolhouse.errors import Denied
from poolhouse.files import write_json
from poolhouse.net import cli as net_cli
from poolhouse.sentinel import permit
from poolhouse.serve import wired, wired_apply
from poolhouse.workspace import authority_cli
from tests import keystore_support

pytest_plugins = ["node_kit"]
board = _board_fixture
counting = keystore_support.counting
LEAD = {"CLAUDECODE": "1", "POOLHOUSE_WORKSPACE_AGENT": "claude-code"}


@pytest.fixture(autouse=True)
def registry(monkeypatch, tmp_path):
    monkeypatch.delenv(authority.FLOOR_ENV, raising=False)
    for name in (*caller.AGENT_MARKERS, "POOLHOUSE_WORKSPACE_AGENT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    folder = tmp_path / "state" / "node" / "client" / "board"
    folder.mkdir(parents=True)
    (folder / "claude-code.token").write_text("t\n", encoding="utf-8")
    (folder / "claude-code.parent").write_text("\n", encoding="utf-8")


@pytest.fixture
def authenticated(workspace_node, monkeypatch):
    actor = workspace_node.member("lead")
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", actor.name)
    monkeypatch.setenv("CLAUDECODE", "1")
    return actor


def uses():
    return [row for row in authority.audit_rows() if row["event"] == "authority.use"]


def events(name):
    return [row for row in caller.audit_rows() if row["event"] == name]


def test_every_gate_names_a_group_and_is_delegated_until_set():
    assert authority.GATES["serve.wired-limit"] == "serve"
    assert {row["state"] for row in authority.show()["gates"]} == {authority.DELEGATED}


def test_set_selects_all_a_group_or_a_gate_and_persists():
    authority.set_state(["sentinel"], authority.PERSON, by="lead")
    assert authority.state_of("sentinel.quarantine") == authority.state_of("sentinel.policy") == authority.PERSON
    assert authority.state_of("serve.wired-limit") == authority.DELEGATED
    authority.set_state(["ALL"], authority.PERSON, by="lead")
    authority.set_state(["serve.wired-limit", "chat.policy"], authority.DELEGATED, by="lead")
    held = {row["gate"]: row["state"] for row in authority.show()["gates"]}
    assert held["serve.wired-limit"] == held["chat.policy"] == authority.DELEGATED
    assert held["sentinel.policy"] == authority.PERSON


def test_an_unknown_name_is_refused_and_changes_nothing():
    with pytest.raises(KeyError):
        authority.set_state(["ALL", "nonsense"], authority.PERSON, by="lead")
    assert authority.state_of("chat.policy") == authority.DELEGATED


def test_a_project_has_its_own_state_over_the_machine_s():
    authority.set_state(["chat.policy"], authority.PERSON, by="lead", scope=authority.Scope(project="alpha"))
    assert authority.state_of("chat.policy", "alpha") == authority.PERSON
    assert authority.state_of("chat.policy", "beta") == authority.state_of("chat.policy") == authority.DELEGATED


def test_the_floor_variable_can_only_tighten(monkeypatch):
    monkeypatch.setenv(authority.FLOOR_ENV, "person")
    assert authority.state_of("serve.wired-limit") == authority.PERSON
    monkeypatch.setenv(authority.FLOOR_ENV, "delegated")
    authority.set_state(["ALL"], authority.PERSON, by="lead")
    assert authority.state_of("serve.wired-limit") == authority.PERSON


def test_a_helper_identity_cannot_flip_and_a_flip_is_audited():
    with pytest.raises(AgentRefused):
        authority.set_state(["ALL"], authority.PERSON, by="lead/helper")
    assert authority.state_of("chat.policy") == authority.DELEGATED
    authority.set_state(["chat.policy"], authority.PERSON, by="lead")
    row = authority.audit_rows()[-1]
    assert (row["by"], row["gates"], row["from"], row["to"]) == ("lead", ["chat.policy"], ["delegated"], "person")
    assert row["who"].startswith("user ")


def test_a_person_state_refuses_an_agent_at_once_and_runs_for_a_process_with_no_marker():
    authority.set_state(["ALL"], authority.PERSON, by="lead")
    with pytest.raises(AgentRefused, match="held back from agents"):
        authority.require("sentinel.policy", "mode", LEAD)
    assert not uses()
    (refusal,) = events("refused")
    assert refusal["action"] == "mode" and "agent CLAUDECODE" in refusal["who"] and "held back" in refusal["why"]
    assert authority.require("sentinel.policy", "mode", {}) == authority.PERSON
    (ran,) = events("ran")
    assert ran["action"] == "mode" and ran["who"].startswith("user ")


def test_a_delegated_gate_passes_a_lead_agent_and_audits_each_use(authenticated):
    assert authority.require("sentinel.policy", "mode") == authority.DELEGATED
    authority.require("sentinel.policy", "mode")
    assert [(u["gate"], u["agent"]) for u in uses()] == [("sentinel.policy", authenticated.name)] * 2
    assert all("agent CLAUDECODE" in u["who"] for u in uses())


def test_a_delegated_gate_passes_every_caller_at_once_and_audits_who_ran_it():
    helper = {**LEAD, "POOLHOUSE_WORKSPACE_AGENT": "claude-code/reader"}
    for env in (helper, {"POOLHOUSE_NONINTERACTIVE": "1"}, {}):
        assert authority.require("sentinel.policy", "mode", env) == authority.DELEGATED
    assert not events("refused")
    first, second, third = uses()
    assert "agent CLAUDECODE" in first["who"] and first["agent"] == ""
    assert "agent POOLHOUSE_NONINTERACTIVE" in second["who"]
    assert third["who"].startswith("user ") and "agent" not in third


def test_a_delegated_quarantine_gate_permits_an_agent_and_a_person_gate_refuses_it_at_once(authenticated, monkeypatch):
    permit.mint_gated("sentinel.quarantine", "release", "q1").check("release", "q1")
    authority.set_state(["sentinel.quarantine"], authority.PERSON, by="lead")
    with pytest.raises(AgentRefused, match="held back from agents"):
        permit.mint_gated("sentinel.quarantine", "release", "q1")
    assert events("refused")[-1]["action"] == "release q1"
    for marker in caller.AGENT_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    permit.mint_gated("sentinel.quarantine", "release", "q1").check("release", "q1")
    assert events("ran")[-1]["action"] == "release q1"


def wired_call(env, via):
    runs = []

    def runner(argv, capture):
        runs.append(argv)
        return 0, "", ""

    hooks = wired.Hooks(total=64 * 1024**3, current=0, others=0, env=env, runner=runner,
                        system="Darwin", read=lambda: 40960)
    return wired_apply.set_limit(40960, via=via, hooks=hooks), runs


def test_the_wired_limit_runs_for_a_delegated_lead_by_either_route_and_is_refused_at_once_when_held_back(authenticated):
    lead = {**LEAD, "POOLHOUSE_WORKSPACE_AGENT": authenticated.name}
    done, runs = wired_call(lead, "osascript")
    assert done.ok and runs[0][0] == "osascript" and "with administrator privileges" in runs[0][-1]
    done, runs = wired_call(lead, "sudo")
    assert done.ok and runs[0][:2] == ["sudo", "/bin/sh"]
    assert [u["agent"] for u in uses()] == [authenticated.name] * 2
    authority.set_state(["serve.wired-limit"], authority.PERSON, by="lead")
    refused = []

    def never(argv, capture):
        refused.append(argv)
        return 0, "", ""

    hooks = wired.Hooks(total=64 * 1024**3, current=0, others=0, env=lead, runner=never, system="Darwin", read=lambda: 40960)
    with pytest.raises(AgentRefused, match="held back from agents"):
        wired_apply.set_limit(40960, via="osascript", hooks=hooks)
    assert not refused


def test_the_wired_limit_runs_without_a_terminal_for_a_process_with_no_agent_marker():
    done, runs = wired_call({}, "sudo")
    assert done.ok and runs[0][:2] == ["sudo", "/bin/sh"]
    (row,) = uses()
    assert row["gate"] == "serve.wired-limit" and row["who"].startswith("user ")


def test_chat_policy_refuses_human_only_wording_only_while_it_is_a_person_s():
    text = "release the thing from quarantine"
    assert chatpolicy.refusal_for(text) is None
    authority.set_state(["chat.policy"], authority.PERSON, by="lead")
    assert chatpolicy.refusal_for(text)[0].startswith("release or purge")


PERSON_ONLY = ("keystore", "passphrase", "sudoers", "signing", "pool-token", "init", "review", "os-prompt")


def test_secrets_and_the_os_prompt_have_no_gate_in_the_registry():
    assert not [g for g in authority.GATES if any(word in g for word in PERSON_ONLY)]


def test_the_keystore_reset_is_refused_to_an_agent_whatever_the_registry_says(monkeypatch, capsys, counting):
    store = keystore.default()
    store.provision()
    held = dict(counting.held)
    assert held
    authority.set_state(["ALL"], authority.DELEGATED, by="lead")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert net_cli.keystore_reset(argparse.Namespace()) == 2
    assert "refused to an agent" in capsys.readouterr().err
    assert counting.held == held and "delete" not in counting.calls
    assert events("refused")[-1]["action"] == "keystore-reset"
    monkeypatch.delenv("CLAUDECODE")
    assert net_cli.keystore_reset(argparse.Namespace()) == 0
    assert not counting.held and events("ran")[-1]["action"] == "keystore-reset"


def test_what_no_registry_name_may_open_is_refused_at_its_own_call_and_never_reads_the_registry():
    from pathlib import Path
    root = Path(authority.__file__).parent
    pinned = {"net/cli.py": 'refuse_agent("keystore-reset"', "fleet/runtime_paths.py": "if not caller.marked():"}
    for name, call in pinned.items():
        assert call in (root / name).read_text()
    assert not re.search(r"authority\.require\([^)]*(keystore|passphrase|unlock)", (root / "net/cli.py").read_text())


def args(action, *words, project=""):
    return argparse.Namespace(action=action, words=list(words), project=project)


def flips():
    return [row for row in authority.audit_rows() if row["event"] == "authority.set"]


def test_show_set_and_preset_through_the_workspace_command(board):
    shown = authority_cli.run(args("show"), board.parent)
    assert {row["state"] for row in shown["gates"]} == {authority.DELEGATED}
    done = authority_cli.run(args("set", "person", "sentinel", "serve.wired-limit"), board.parent)
    assert {c["gate"] for c in done["changes"]} == {"sentinel.quarantine", "sentinel.policy", "serve.wired-limit"}
    assert authority.state_of("serve.wired-limit") == authority.PERSON
    flip = flips()[-1]
    assert (flip["by"], flip["from"], flip["to"]) == (board.lead.name, ["delegated"], "person")


def test_prod_makes_the_sensitive_gates_a_person_s_and_enforcement_strict_and_dev_undoes_both(board, monkeypatch):
    prod = authority_cli.run(args("preset", "prod", project="alpha"), board.parent)
    assert prod["enforcement"]["mode"] == "strict" and Tasks(board.parent).mode()["mode"] == "strict"
    assert prod["preflight"]["clear"] and prod["preflight"]["stranded"] == []
    assert authority.state_of("sentinel.quarantine", "alpha") == authority.PERSON
    assert authority.state_of("workspace.setup", "alpha") == authority.DELEGATED
    dev = authority_cli.run(args("preset", "dev", project="alpha"), board.parent)
    assert dev["enforcement"]["mode"] == "open" and Tasks(board.parent).mode()["mode"] == "open"
    assert {row["state"] for row in authority.show("alpha")["gates"]} == {authority.DELEGATED}
    assert authority.show()["preset"] == "dev"


def test_a_helper_identity_cannot_set_or_preset(board):
    for call in (args("set", "person", "ALL"), args("preset", "prod", project="alpha")):
        with pytest.raises(Denied):
            authority_cli.run(call, board.child)
    assert authority.state_of("sentinel.quarantine") == authority.DELEGATED
    assert Tasks(board.parent).mode()["mode"] == "open"
    assert not flips()


def test_a_helper_refused_a_change_of_authority_leaves_an_audit_entry_on_the_board(board):
    for call in (args("set", "person", "ALL"), args("preset", "prod", project="alpha")):
        with pytest.raises(Denied):
            authority_cli.run(call, board.child)
    told = [(e.sender, e.fields["subject"]) for e in board.parent.read_all(kind="audit") if e.fields["event"] == "authority.set_denied"]
    assert told == [(board.child.name, "set person ALL"), (board.child.name, "preset prod")]
    authority_cli.run(args("set", "person", "sentinel"), board.parent)
    assert len([e for e in board.parent.read_all(kind="audit") if e.fields["event"] == "authority.set_denied"]) == 2


def test_a_bad_command_is_refused_with_a_message(board):
    for call in (args("set", "maybe", "ALL"), args("set", "person"), args("set", "person", "nope"), args("preset", "x"),
                 args("preset")):
        with pytest.raises(ValueError):
            authority_cli.run(call, board.parent)


@pytest.mark.parametrize("gate", ["runtime.deploy-unmerged", "github.release"])
def test_new_gate_honors_an_existing_prod_preset(monkeypatch, gate):
    write_json(authority._file(), {"preset": "prod", "gates": {"runtime.deploy": "person"}})
    assert authority.state_of(gate) == authority.PERSON
    assert authority.state_of("workspace.setup") == authority.DELEGATED
    write_json(authority._file(), {"preset": "prod", "gates": {gate: "delegated"},
                                   "projects": {"alpha": {gate: "person"}}})
    assert authority.state_of(gate) == authority.DELEGATED
    assert authority.state_of(gate, "alpha") == authority.PERSON
    monkeypatch.setenv(authority.FLOOR_ENV, authority.PERSON)
    assert authority.state_of(gate) == authority.PERSON

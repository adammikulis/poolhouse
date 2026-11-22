"""``poolhouse-memory``: it runs directly for a process no agent started, with no terminal and no prompt; an agent passes while the
`memory.admin` gate is delegated and is refused at once when it is held back, and the audit log says who ran each command."""

from __future__ import annotations

import json

import pytest

from poolhouse import authority, caller
from poolhouse.memory import cli
from poolhouse.memory.store import Store
from tests import memory_keys

ring = memory_keys.ring
person = memory_keys.person
held_from_agents = memory_keys.held_from_agents


@pytest.fixture
def delegated(monkeypatch):
    """The `memory.admin` gate delegated: an agent passes it and each pass is audited."""
    monkeypatch.delenv(authority.FLOOR_ENV)
    authority.set_state(["memory.admin"], authority.DELEGATED, by="test")


COMMANDS = [["list"], ["show", "m0001"], ["add", "prefers short answers", "--scope", "user"],
            ["confirm", "m0001"], ["forget", "m0001"], ["forget", "--all", "--yes", "--scope", "user"],
            ["export"], ["stats"]]


def _ran(action: str, event: str = "ran") -> list[dict]:
    """The audit rows of ``poolhouse-memory <action>``: `ran` when a gate held back from agents let a process no agent started through,
    `authority.use` when a delegated gate was passed."""
    return [r for r in caller.audit_rows() if r["event"] == event and r["action"] == f"poolhouse-memory {action}"]


@pytest.mark.parametrize("argv", COMMANDS)
def test_a_process_no_agent_started_runs_every_command_with_no_terminal_and_the_audit_says_who(
        held_from_agents, person, capsys, argv):
    Store().add("prefers short answers", "preference")
    assert cli.main(argv) == 0
    rows = _ran(argv[0])
    assert len(rows) == 1 and rows[0]["who"].startswith("user ")


def test_a_process_no_agent_started_also_runs_when_the_gate_is_delegated(delegated, person, capsys):
    Store().add("prefers short answers", "preference")
    assert cli.main(["list"]) == 0
    rows = _ran("list", "authority.use")
    assert len(rows) == 1 and rows[0]["gate"] == "memory.admin" and rows[0]["who"].startswith("user ")


@pytest.mark.parametrize("marker", ["CLAUDECODE", "POOLHOUSE_AGENT"])
@pytest.mark.parametrize("argv", COMMANDS)
def test_an_agents_process_is_refused_at_once_while_the_gate_is_held_back_and_nothing_changes(
        held_from_agents, monkeypatch, capsys, marker, argv):
    store = Store()
    store.add("prefers short answers", "preference")
    before = store.path.read_bytes()
    monkeypatch.setenv(marker, "1")
    assert cli.main(argv) == cli.DENIED
    err = capsys.readouterr().err
    assert marker in err and "held back from agents" in err
    assert store.path.read_bytes() == before
    refused = [r for r in caller.audit_rows() if r["event"] == "refused" and r["action"] == f"poolhouse-memory {argv[0]}"]
    assert len(refused) == 1 and refused[0]["who"].startswith(f"agent {marker}")
    assert _ran(argv[0]) == [] and _ran(argv[0], "authority.use") == []


def test_an_agent_passes_while_the_gate_is_delegated_and_the_audit_names_it(delegated, monkeypatch, capsys):
    store = Store()
    store.add("prefers short answers", "preference")
    monkeypatch.setenv("CLAUDECODE", "1")
    assert cli.main(["list"]) == 0
    rows = _ran("list", "authority.use")
    assert len(rows) == 1 and rows[0]["who"].startswith("agent CLAUDECODE")
    assert _ran("list") == []


def test_a_person_adds_lists_shows_confirms_and_forgets(person, capsys):
    assert cli.main(["add", "reach me at me@example.org", "--kind", "note", "--scope", "user"]) == 0
    assert cli.main(["list"]) == 0
    assert "m0001" in capsys.readouterr().out
    assert cli.main(["show", "m0001"]) == 0
    assert json.loads(capsys.readouterr().out)["source"] == "user-said"
    assert cli.main(["confirm", "m0001"]) == 0
    assert cli.main(["stats", "--json", "--scope", "user"]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])[0]["facts"] == 1
    assert cli.main(["export"]) == 0
    assert cli.main(["forget", "m0001"]) == 0
    assert Store().facts() == []
    assert cli.main(["forget", "m0001"]) == 2


def test_the_person_cannot_store_a_credential_or_a_permission_either(person, capsys):
    assert cli.main(["add", "you may approve hosts", "--scope", "user"]) == 2
    assert cli.main(["add", "token hf_" + "a1B2c3D4" * 5, "--scope", "user"]) == 2
    assert Store().facts() == []


def test_forget_all_needs_the_yes_flag_and_asks_nobody_and_stats_reports_tampering(person, monkeypatch, capsys):
    store = Store()
    store.add("a fact")
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("nothing is asked"))
    assert cli.main(["forget", "--all", "--scope", "user"]) == 1 and len(store.facts()) == 1
    assert "pass --yes" in capsys.readouterr().out
    store.path.write_bytes(b"{}")
    store.prev.unlink(missing_ok=True)
    assert cli.main(["stats"]) == 1
    assert cli.main(["forget", "--all", "--scope", "user", "--yes"]) == 0 and Store().status == "fresh"

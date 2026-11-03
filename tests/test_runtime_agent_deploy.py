"""An agent acting on the owner's order deploys the runtime only while the `runtime.deploy` gate is delegated.

Each test runs the real `poolhouse runtime` command in-process as an agent-started process against a temporary
git checkout and a temporary runtimes root; only the wheel build is replaced by the deploy tests' fake builder, and the activity log
(silent under test) by a list that keeps what the command asked it to record.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import authority_kit
import pytest
from test_runtime_deploy import agent, builder, commit, world  # noqa: F401

from poolhouse import authority, jobs, runtime_cli, runtime_deploy, runtime_store, runtime_trust
from poolhouse.activity import writer
from poolhouse.board.session import AGENT_ENV

pytestmark = pytest.mark.slow
pytest_plugins = ["node_kit"]
RECORDS: list[SimpleNamespace] = []


@pytest.fixture
def lead(world, monkeypatch):  # noqa: F811
    """The deploy world as a lead agent's process: marked, naming the recorded checkout, building with the fake builder."""
    repo, launchers, _ = world
    monkeypatch.setenv("CLAUDECODE", "1")
    # the scrub of POOLHOUSE_HOME has its own tests (test_runtime_trust); here the state root must stay the temporary one
    monkeypatch.setattr(runtime_trust, "ignore_redirects", lambda: [])
    monkeypatch.delenv("POOLHOUSE_AUTHORITY_FLOOR", raising=False)
    runtime_deploy.prepare_root()
    runtime_store.write_state({"checkout": str(repo), "launchers": str(launchers)})
    RECORDS.clear()
    monkeypatch.setattr(writer, "record", lambda kind, **said: RECORDS.append(SimpleNamespace(kind=kind, **said)) or True)
    fake = {"build": builder()}
    real = runtime_deploy.ensure
    monkeypatch.setattr(runtime_deploy, "ensure", lambda plan, **kw: real(plan, builder=fake["build"], **kw))
    return repo, launchers, fake


def deploys():
    return [row for row in RECORDS if row.kind == "runtime.deploy"]


def selected():
    return runtime_store.selection().get("commit", "")


def test_a_delegated_agent_s_ensure_deploys_and_is_audited_with_the_agent(lead, agent, capsys):  # noqa: F811
    repo, _, _ = lead
    head = commit(repo, "a")
    assert runtime_cli.main(["ensure"]) == 0, capsys.readouterr().err
    assert selected() == head
    [entry] = deploys()
    assert (entry.outcome, entry.meta["command"], entry.meta["agent"]) == ("switched", "ensure", agent.name)
    assert entry.meta["person"] is False and entry.meta["authority"] == authority.DELEGATED
    used = [row for row in authority.audit_rows() if row["event"] == "authority.use"]
    assert [(row["gate"], row["agent"]) for row in used] == [("runtime.deploy", agent.name)] * 2


def test_an_agent_is_refused_while_the_gate_is_the_person_s_and_nothing_is_built(lead, agent, capsys):  # noqa: F811
    repo, _, _ = lead
    commit(repo, "a")
    authority.set_state(["runtime.deploy"], authority.PERSON, by="lead")
    assert runtime_cli.main(["ensure"]) == 1
    assert "for a person" in capsys.readouterr().err
    assert selected() == "" and not runtime_store.candidates()
    [entry] = deploys()
    assert (entry.outcome, entry.meta["command"]) == ("refused", "ensure") and entry.meta["agent"] == agent.name


def test_revoking_the_delegation_refuses_the_next_deploy_and_keeps_the_selection(lead):
    repo, _, _ = lead
    first = commit(repo, "a")
    assert runtime_cli.main(["ensure"]) == 0
    authority.set_state(["runtime.deploy"], authority.PERSON, by="lead")
    commit(repo, "b")
    assert runtime_cli.main(["ensure"]) == 1
    assert selected() == first
    authority_kit.delegate(["runtime.deploy"])
    assert runtime_cli.main(["ensure"]) == 0 and selected() != first


def test_rollback_follows_the_same_gate(lead):
    repo, _, _ = lead
    first = commit(repo, "a")
    runtime_cli.main(["ensure"])
    commit(repo, "b")
    runtime_cli.main(["ensure"])
    authority.set_state(["runtime.deploy"], authority.PERSON, by="lead")
    assert runtime_cli.main(["rollback"]) == 1 and selected() != first
    authority_kit.delegate(["runtime.deploy"])
    assert runtime_cli.main(["rollback"]) == 0 and selected() == first
    assert [e.meta["command"] for e in deploys()].count("rollback") == 2


def test_a_failed_smoke_under_a_delegated_deploy_leaves_the_selection_alone(lead):
    repo, _, fake = lead
    launchers = runtime_cli._launchers("")
    first = commit(repo, "a")
    runtime_cli.main(["ensure"])
    before = (launchers / "poolhouse-workspace").read_bytes()
    commit(repo, "b")
    fake["build"] = builder(hook_code=3)
    assert runtime_cli.main(["ensure"]) == 1
    assert selected() == first and (launchers / "poolhouse-workspace").read_bytes() == before
    assert deploys()[-1].outcome == "failed"


def test_a_process_no_agent_started_is_not_gated(lead, monkeypatch):
    repo, _, _ = lead
    head = commit(repo, "a")
    monkeypatch.delenv("CLAUDECODE")
    monkeypatch.delenv(AGENT_ENV)
    authority.set_state(["ALL"], authority.PERSON, by="lead")
    assert runtime_cli.main(["ensure"]) == 0 and selected() == head


def the_background_start(monkeypatch):
    """What `ensure --background` asks `jobs.detach` to start, and the environment it would start it in; nothing is started."""
    asked = {}

    def detach(module, argv, **_named):
        asked.update(module=module, argv=list(argv), env=dict(os.environ))
        return SimpleNamespace(pid=0)

    monkeypatch.setattr(jobs, "detach", detach)
    return asked


def test_a_lead_with_no_session_refreshes_to_a_published_commit_in_the_background_as_the_system(lead, agent, monkeypatch, capsys):  # noqa: F811
    repo, _, _ = lead
    first = commit(repo, "a")
    assert runtime_cli.main(["ensure"]) == 0 and selected() == first
    head = commit(repo, "b")
    for name in (AGENT_ENV, "POOLHOUSE_BOARD"):
        monkeypatch.delenv(name)
    asked = the_background_start(monkeypatch)
    assert runtime_cli.main(["ensure", "--background", "--now"]) == 0, capsys.readouterr().err
    assert asked["module"] == "poolhouse.runtime_cli" and not set(runtime_trust.IDENTITY) & asked["env"].keys(), "the build carries no agent"
    assert os.environ["CLAUDECODE"] == "1", "and the command that started it keeps its own environment"
    assert "--agent" not in asked["argv"] and selected() == first
    for name in runtime_trust.IDENTITY:
        monkeypatch.delenv(name, raising=False)
    RECORDS.clear()
    assert runtime_cli.main(asked["argv"]) == 0 and selected() == head
    [entry] = deploys()
    assert (entry.actor, entry.outcome, entry.meta["agent"]) == ("system:runtime", "switched", "")


def test_a_background_refresh_needs_no_delegation_and_a_subagent_starts_none(lead, agent, monkeypatch, capsys):  # noqa: F811
    repo, _, _ = lead
    commit(repo, "a")
    asked = the_background_start(monkeypatch)
    authority.set_state(["runtime.deploy"], authority.PERSON, by="lead")
    monkeypatch.setenv(authority.SUBAGENT_ENV, "helper")
    assert runtime_cli.main(["ensure", "--background", "--now"]) == 1 and "subagent" in capsys.readouterr().err and not asked
    monkeypatch.delenv(authority.SUBAGENT_ENV)
    assert runtime_cli.main(["ensure", "--background", "--now"]) == 0 and asked["argv"][0] == "ensure", "the gate is the person's and the refresh goes on"


def test_dev_delegates_the_deploy_gate_and_prod_keeps_it_with_the_person(tmp_path, monkeypatch):
    monkeypatch.setenv("POOLHOUSE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("POOLHOUSE_AUTHORITY_FLOOR", raising=False)
    assert authority.GATES["runtime.deploy"] == "runtime"
    assert "runtime.deploy" not in authority.PROD_DELEGATED
    assert authority.state_of("runtime.deploy") == authority.DELEGATED


def test_restart_host_follows_the_same_gate_and_is_audited(lead, capsys):
    repo, _, _ = lead
    commit(repo, "a")
    runtime_cli.main(["ensure"])
    authority.set_state(["runtime.deploy"], authority.PERSON, by="lead")
    assert runtime_cli.main(["restart-host", "--port", "1"]) == 1
    assert deploys()[-1].outcome == "refused" and deploys()[-1].meta["command"] == "restart-host"
    authority_kit.delegate(["runtime.deploy"])
    assert runtime_cli.main(["restart-host", "--port", "1"]) == 0
    assert "not-running" in capsys.readouterr().out
    last = deploys()[-1]
    assert (last.outcome, last.meta["command"], last.meta["authority"]) == ("not-running", "restart-host", authority.DELEGATED)


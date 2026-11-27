"""The launch gate, attacked after its own review (docs/notes/attack-verdict-isolation-l2-l3.md, H2 and H3): who is asking is read from the
process tree by a real node, the roots of the gate, the audit and the policy are the account's own, and the person's policy is the registered
project's wherever the launch names it. Real processes, a real node and real terminals; a refusal is immediate and nothing is answered.
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from poolhouse import authority, node_launch
from poolhouse.isolation import Arguments, IsolationRefused, decide_launch, policy as policies
from poolhouse.isolation.policy import ProjectPolicy
from tests.isolation_kit import SOURCE, person_home, registered_project

pytest_plugins = ["node_kit"]
MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", "CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID", "POOLHOUSE_SESSION_ID")
GATE = """
import pytest  # a test run: the state root may be moved to the node of the test
from poolhouse import caller
from poolhouse.isolation import require_widening
try:
    require_widening("a wider launch", None, %(project)r)
    print("RESULT passed")
except caller.AgentRefused as why:
    print("RESULT refused", str(why)[:160])
"""
PATHS = """
import json
from poolhouse import authority, home
from poolhouse.isolation import ancestry, policy
outside = [str(authority._file()), str(home.state("node"))]
with home.using_real_roots():
    inside = [str(authority._file()), str(policy.path_of("project")), str(home.guarded("authority-audit.jsonl")), str(ancestry.node_state()),
              str(home.under_test())]
print("PATHS" + json.dumps(inside + outside))
"""


def program(code: str) -> list[str]:
    return [sys.executable, "-c", f"import sys; sys.path.insert(0, {SOURCE!r})\n" + code]


@pytest.fixture
def terminal(tmp_path, monkeypatch, workspace_node):
    """A caller outside any agent on a real node, with a git project the node holds."""
    for name in (*MARKERS, "POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_SUBAGENT", authority.FLOOR_ENV):
        monkeypatch.delenv(name, raising=False)
    person_home(tmp_path, monkeypatch)
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    monkeypatch.setenv("POOLHOUSE_BOARD", workspace_node.board)
    return registered_project(tmp_path, workspace_node)


@pytest.fixture
def lead(terminal, workspace_node, monkeypatch):
    """The live main session of the node, registered from this test's own process tree, and this process marked and named as it."""
    actor = workspace_node.member("lead")
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", actor.name)
    monkeypatch.setenv("CLAUDECODE", "1")
    return actor


def said(text: str, prefix: str = "RESULT ") -> str:
    return next(line for line in text.splitlines() if line.startswith(prefix))[len(prefix):]


def descendant(code: str, env: dict[str, str]) -> str:
    """A child of this test process, which is in the tree the lead was registered from, run with exactly the environment ``env``."""
    done = subprocess.run(program(code), env=env, capture_output=True, text=True, timeout=90, check=False)
    assert "RESULT" in done.stdout or "PATHS" in done.stdout, done.stdout + done.stderr[-1500:]
    return done.stdout


def bare(node, **more: str) -> dict[str, str]:
    """What `env -i poolhouse ...` leaves: no marker, no session variable, nothing but the way to the code and to this test's node."""
    return {"PATH": "/usr/bin:/bin", "PYTHONPATH": SOURCE, "POOLHOUSE_HOME": str(node.state.parent), "HOME": os.environ["HOME"], **more}


def test_an_agent_that_runs_the_launcher_with_a_clean_environment_is_still_an_agent_and_the_person_gate_refuses_it(terminal, workspace_node):
    authority.set_state(["isolation.grant"], authority.PERSON, by="lead", env={})
    _, shown, errors = workspace_node.terminal(program=program(GATE % {"project": terminal}))
    assert said(shown).startswith("passed"), f"the control: a person's terminal outside any agent passes the person gate: {shown} {errors}"
    workspace_node.member("lead")  # the lead registers from this test process, and every process started here is in its tree
    shown = descendant(GATE % {"project": terminal}, bare(workspace_node))
    assert said(shown).startswith("refused") and "held back from agents" in shown, shown


def test_a_process_that_is_not_in_the_leads_tree_does_not_become_the_lead_by_naming_it(terminal, workspace_node):
    actor = workspace_node.member("lead")
    named = {"CLAUDECODE": "1", "POOLHOUSE_WORKSPACE_AGENT": actor.name, "POOLHOUSE_BOARD": workspace_node.board}
    inside = descendant(GATE % {"project": terminal}, bare(workspace_node, **named))
    assert said(inside).startswith("passed"), "the control: the lead's own process passes"
    _, shown, errors = workspace_node.terminal(program=program(GATE % {"project": terminal}), env=named)
    assert said(shown).startswith("refused") and "finds no session above it" in shown, (shown, errors)


def test_a_helpers_marker_or_name_passes_in_a_sessions_tree_and_places_no_process_outside_it(terminal, workspace_node):
    lead_session = workspace_node.member("lead")
    kid = workspace_node.member("kid", parent=lead_session)
    for more in ({"POOLHOUSE_SUBAGENT": "kid"}, {"POOLHOUSE_WORKSPACE_AGENT": kid.name}):
        shown = descendant(GATE % {"project": terminal}, bare(workspace_node, **more))
        assert said(shown).startswith("passed"), (more, shown)
        _, outside, errors = workspace_node.terminal(program=program(GATE % {"project": terminal}), env={"CLAUDECODE": "1", **more})
        assert said(outside).startswith("refused") and "finds no session above it" in outside, (more, outside, errors)
    assert said(descendant(GATE % {"project": terminal}, bare(workspace_node))).startswith("passed"), "no marker: the lead's own tree"


def test_a_node_that_does_not_answer_refuses_the_launch_at_once_with_the_reason(terminal, tmp_path):
    empty = tmp_path / "no-node"
    (empty / "node").mkdir(parents=True)
    began = time.monotonic()
    shown = descendant(GATE % {"project": terminal}, {"PATH": "/usr/bin:/bin", "PYTHONPATH": SOURCE, "POOLHOUSE_HOME": str(empty), "HOME": str(tmp_path)})
    assert said(shown).startswith("refused") and "the node did not answer" in shown and time.monotonic() - began < 30, shown


def test_the_roots_of_the_gate_the_audit_the_policy_and_the_node_are_the_accounts_own_whatever_the_environment_names(tmp_path):
    account = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve() / ".poolhouse"
    redirect = tmp_path / "redirected"
    redirect.mkdir()
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": SOURCE, "POOLHOUSE_HOME": str(redirect), "HOME": str(tmp_path / "fake-home")}
    real = json.loads(said(descendant(PATHS, env), "PATHS"))
    assert real[0] == str(account / "authority.json") and real[2] == str(account / "authority-audit.jsonl") and real[3] == str(account / "node"), real
    assert Path(real[1]).parent == account / "isolation" / "projects" and real[4] == "False"
    assert real[5] == str(redirect / "authority.json") and real[6] == str(redirect / "node"), "everything outside the launch gate follows the environment, as before"
    testing = json.loads(said(descendant("import pytest\n" + PATHS, env), "PATHS"))
    assert testing[0] == str(redirect / "authority.json") and testing[3] == str(redirect / "node") and testing[4] == "True", "a test run keeps its isolation"


# H3: the person's policy belongs to the registered project, not to the exact path a launch names


def commit(project: str) -> None:
    subprocess.run(["git", "-C", project, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, capture_output=True)


def test_a_worktree_of_the_registered_project_in_the_temp_folder_has_the_projects_policy(terminal, tmp_path, request):
    policies.save(terminal, ProjectPolicy(net=("pypi.org",)))
    request.getfixturevalue("lead")
    commit(terminal)
    sibling = str(tmp_path.parent / f"{tmp_path.name}-worktree")
    subprocess.run(["git", "-C", terminal, "worktree", "add", "-q", sibling], check=True, capture_output=True)
    assert policies.key_of(sibling) == policies.key_of(terminal) != "", "the node resolves a worktree to the registered project"
    for project in (terminal, sibling):
        launch = decide_launch(Arguments(read=("/usr/share",), allow_net=("pypi.org",)), project=project)
        assert [each.granted for each in launch.decisions] == [False, True], project
        assert launch.decisions[0].cause == "outside the project policy", "the person's policy bounds the read a task names"


def test_a_copy_of_the_project_is_a_project_of_its_own_that_a_helper_registers_and_a_folder_that_is_no_repository_is_refused(
        lead, terminal, workspace_node, tmp_path, monkeypatch):
    copy = tmp_path / "copy"
    subprocess.run(["cp", "-R", terminal, str(copy)], check=True)
    assert policies.key_of(str(copy)) == "", "a copy holds its own git directory: another repository, which the node does not hold"
    kid = workspace_node.member("kid", parent=lead)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", kid.name)
    decide_launch(Arguments(read=("/usr/share",)), project=str(copy))
    assert policies.key_of(str(copy)) not in ("", policies.key_of(terminal)), "the helper's launch made the copy a project of its own"
    nowhere = Path(tempfile.gettempdir()).resolve() / "ph-attack-not-registered"
    nowhere.mkdir(exist_ok=True)
    with pytest.raises(IsolationRefused, match="this folder is not one"):
        decide_launch(Arguments(project=str(nowhere)), project=terminal)


def test_the_project_the_lead_runs_in_is_registered_by_the_launch_when_the_node_did_not_hold_it_yet(lead, workspace_node, tmp_path, monkeypatch):
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    subprocess.run(["git", "init", "-q", str(fresh)], check=True, capture_output=True)
    assert policies.key_of(str(fresh)) == ""
    monkeypatch.chdir(fresh)
    launch = decide_launch(Arguments(project=str(fresh), allow_net=("pypi.org",)), project=str(fresh))
    assert launch.widened and policies.key_of(str(fresh)) != "", "no terminal step: the lead's own project is added by the node"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    subprocess.run(["git", "init", "-q", str(elsewhere)], check=True, capture_output=True)
    decide_launch(Arguments(project=str(elsewhere)), project=str(elsewhere))
    assert policies.key_of(str(elsewhere)) not in ("", policies.key_of(str(fresh))), "a repository the lead names is registered too"

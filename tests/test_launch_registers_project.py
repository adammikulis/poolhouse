"""A launch registers the project it works in: the lead (or a terminal) names a git repository, by `--project` or by the folder it runs in, and the
node holds it when the launch ends; a subagent or an agent the node cannot place gets no registration. Registering widens nothing, what no session
is given is refused at once with its reason, and a project can be removed at once. A real node, real git and real processes; nothing waits and
no test answers anything. No model is involved: the gate decides before one is leased.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from poolhouse import authority, node_launch, sentinel
from poolhouse.caller import AgentRefused
from poolhouse.isolation import (
    Arguments,
    IsolationRefused,
    decide_launch,
    policy as policies,
    projects,
)
from poolhouse.isolation.refusals import sentence
from poolhouse.workspace import cli
from tests.confined_kit import repository
from tests.isolation_kit import person_home, registered_project
from tests.isolation_world import git

pytest_plugins = ["node_kit"]
MARKERS = ("CLAUDECODE", "POOLHOUSE_AGENT", "POOLHOUSE_NONINTERACTIVE", "CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID", "POOLHOUSE_SESSION_ID")
REGISTERED = "isolation.project_registered"
REFUSED = r"not a registered project|only the session that launches"
NOW = 15.0
"""The longest any decision of this file may take: a few git and node calls, never a wait."""


@pytest.fixture
def terminal(tmp_path, monkeypatch, workspace_node):
    """A process outside any agent on a real node, with a git project the node holds and a home with credential folders."""
    for name in (*MARKERS, "POOLHOUSE_WORKSPACE_AGENT", "POOLHOUSE_SUBAGENT", authority.FLOOR_ENV):
        monkeypatch.delenv(name, raising=False)
    person_home(tmp_path, monkeypatch)
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    monkeypatch.setenv("POOLHOUSE_BOARD", workspace_node.board)
    return registered_project(tmp_path, workspace_node)


@pytest.fixture
def lead(terminal, workspace_node, monkeypatch):
    """The live main agent session of the node, marked and named, in whose process tree this test runs."""
    actor = workspace_node.member("lead")
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", actor.name)
    monkeypatch.setenv("CLAUDECODE", "1")
    return actor


@pytest.fixture
def subagent(lead, workspace_node, monkeypatch):
    """A helper of the lead, as a subagent runs the launcher in its own shell."""
    kid = workspace_node.member("kid", parent=lead)
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", kid.name)
    return kid


def fresh(tmp_path: Path, name: str = "accept-repo") -> str:
    """A git repository with one committed file that the node does not hold."""
    path = repository(tmp_path / name)
    git(path, "commit", "-q", "-m", "one file")
    return str(path.resolve())


def become(kind: str, request) -> None:
    if kind == "lead":
        request.getfixturevalue("lead")


def held(path: str) -> str:
    """The board of the project the node holds around ``path``, or an empty string."""
    return policies.key_of(path)


@pytest.mark.parametrize("by", ["the project argument", "the folder it runs in"])
def test_the_lead_names_a_repository_and_the_launch_registers_it_logs_it_and_tells_the_sentinel_once(lead, terminal, tmp_path, by):
    named = fresh(tmp_path)
    heard, told = [], []
    sentinel.default().bus.subscribe(heard.append)
    arguments = Arguments(project=named) if by == "the project argument" else Arguments()
    assert held(named) == ""
    began = time.monotonic()
    launch = decide_launch(arguments, project=named, on_event=lambda name, fields: told.append(name))
    assert time.monotonic() - began < NOW
    board = held(named)
    assert board and board != held(terminal), "the node holds the named repository as a project of its own"
    assert not launch.widened and launch.grants == () and launch.decisions == (), "registering is the base reach and widens nothing"
    ours = [event for event in heard if event.kind == REGISTERED]
    assert len(ours) == 1 and told == [REGISTERED] and ours[0].severity.name == "NOTICE"
    assert ours[0].evidence["project"] == board and named not in json.dumps(ours[0].evidence), "the project's id and a digest of its place, never its path"
    assert ours[0].evidence["source"] == ("named" if arguments.project else "working directory") and ours[0].evidence["issuer"].startswith("agent CLAUDECODE")
    again = decide_launch(arguments, project=named, on_event=lambda name, fields: told.append(name))
    assert again.decisions == () and held(named) == board and told == [REGISTERED], "a second launch finds the project and signals nothing"


@pytest.mark.parametrize("kind", ["terminal", "lead"])
def test_a_launch_in_a_repository_the_node_does_not_hold_registers_it_for_the_lead_and_for_a_terminal(kind, terminal, request, tmp_path):
    become(kind, request)
    folder = fresh(tmp_path, "scratch-repo")
    decide_launch(Arguments(), project=folder)
    assert held(folder) != ""
    git(Path(folder), "commit", "-q", "--allow-empty", "-m", "two")
    sibling = str(tmp_path.parent / f"{tmp_path.name}-worktree")
    git(Path(folder), "worktree", "add", "-q", sibling)
    told = []
    decide_launch(Arguments(), project=sibling, on_event=lambda name, fields: told.append(name))
    assert held(sibling) == held(folder) and told == [], "a worktree of a registered project is that project"


def test_a_subagent_or_an_agent_the_node_cannot_place_gets_no_registration_and_a_plain_reason(terminal, tmp_path, monkeypatch, request):
    named = fresh(tmp_path)
    monkeypatch.setenv("CLAUDECODE", "1")  # marked, and in no tree of a session the node holds
    for arguments in (Arguments(), Arguments(project=named)):
        with pytest.raises(AgentRefused, match=REFUSED):
            decide_launch(arguments, project=named)
    monkeypatch.delenv("CLAUDECODE")
    request.getfixturevalue("subagent")
    for arguments in (Arguments(), Arguments(project=named)):
        began = time.monotonic()
        with pytest.raises(AgentRefused, match=REFUSED):
            decide_launch(arguments, project=named)
        assert time.monotonic() - began < NOW
    assert held(named) == "", "the node holds nothing for either"
    decide_launch(Arguments(), project=terminal)  # its own registered project: the default reach, as before


def where_not(case: str, tmp_path: Path) -> str:
    """A folder that no coding session is given, for each reason."""
    house = tmp_path / "home"
    if case == "stage":
        base = Path(os.path.realpath(tempfile.gettempdir())) / f"poolhouse-coding-{uuid.uuid4().hex[:8]}"
        (base / "copy").mkdir(parents=True)
        repository(base / "copy")
        return str(base / "copy")
    if case == "dotfiles":
        (house / "projects" / "app").mkdir(parents=True)
        git(house, "init", "-q")
        return str(house / "projects" / "app")
    return {"home": str(house), "above-home": str(tmp_path), "credentials": str(house / ".ssh"), "aws": str(house / ".aws"), "system": "/usr",
            "etc": "/etc", "plain": str(tmp_path / "plain")}[case]


@pytest.mark.parametrize("case, key", [("home", "project_home"), ("above-home", "project_home"), ("dotfiles", "project_home"),
                                       ("credentials", "project_credential"), ("aws", "project_credential"), ("system", "project_system"),
                                       ("etc", "project_system"), ("stage", "project_stage"), ("plain", "not_a_repository")])
@pytest.mark.parametrize("kind", ["terminal", "lead"])
def test_a_folder_no_session_is_given_is_refused_at_once_with_its_reason_and_the_node_holds_nothing(kind, case, key, request, tmp_path):
    request.getfixturevalue("terminal")
    become(kind, request)
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "notes.txt").write_text("not a repository\n")
    folder = where_not(case, tmp_path)
    if case == "stage":
        request.addfinalizer(lambda: shutil.rmtree(Path(folder).parent, ignore_errors=True))
    heard = []
    for arguments in (Arguments(), Arguments(project=folder)):
        began = time.monotonic()
        with pytest.raises(IsolationRefused) as refused:
            decide_launch(arguments, project=folder, on_event=lambda name, fields: heard.append(name))
        assert str(refused.value) == sentence(key) and time.monotonic() - began < NOW
    assert held(folder) == "" and heard == [], "nothing was registered and nothing was signalled"


def test_the_state_root_of_this_computer_and_a_folder_inside_it_are_refused_as_a_project(lead, workspace_node):
    inside = workspace_node.state.parent / "work"
    inside.mkdir()
    for folder in (str(workspace_node.state.parent), str(inside)):
        with pytest.raises(IsolationRefused) as refused:
            decide_launch(Arguments(), project=folder)
        assert str(refused.value) == sentence("project_state")


def test_registering_a_project_widens_nothing_every_widening_still_needs_its_own_decision(lead, terminal, tmp_path):
    named = fresh(tmp_path)
    house = tmp_path / "home"
    arguments = Arguments(project=named, read=(str(house / ".ssh"),), allow_net=("pypi.org",), in_place=True)
    launch = decide_launch(arguments, project=named)
    assert held(named) != "" and not launch.widened and launch.grants == ()
    assert [each.granted for each in launch.decisions] == [False, False, False], "a credential folder, a host beside in-place, in-place"


def test_in_the_prod_preset_the_lead_registers_the_project_it_works_in_and_every_widening_is_still_held_back(lead, terminal, tmp_path):
    authority.set_state(["isolation.grant"], authority.PERSON, by=lead.name)
    named = fresh(tmp_path)
    decide_launch(Arguments(), project=named)
    assert held(named) != "", "the base reach is not a widening"
    other = fresh(tmp_path, "other-repo")
    with pytest.raises(AgentRefused, match="held back from agents"):
        decide_launch(Arguments(project=other, read=("/usr/share",)), project=other)
    assert held(other) == ""


def test_a_project_is_removed_at_once_and_an_orphan_no_longer_blocks_the_place_around_it(lead, terminal, tmp_path):
    inner = Path(fresh(tmp_path, "outer/repo"))
    decide_launch(Arguments(), project=str(inner))
    board = held(str(inner))
    shutil.rmtree(inner)
    outer = Path(repository(tmp_path / "outer").resolve())
    with pytest.raises(IsolationRefused, match="holds a place of project"):
        decide_launch(Arguments(), project=str(outer))
    began = time.monotonic()
    assert projects.remove(str(inner), issuer="a test") == board, "by the folder, which is gone"
    assert time.monotonic() - began < NOW
    decide_launch(Arguments(), project=str(outer))
    assert held(str(outer)) not in ("", board)
    with pytest.raises(IsolationRefused, match="could not remove"):
        projects.remove(board, issuer="a test")


def run(*argv: str, capsys) -> tuple[int, str]:
    code = cli.main(["project", *argv])
    said = capsys.readouterr()
    return code, said.out + said.err


def test_the_project_command_lists_and_removes_for_the_lead_and_a_terminal_and_refuses_every_other_agent(terminal, tmp_path, capsys, request, monkeypatch):
    named = fresh(tmp_path)
    decide_launch(Arguments(), project=named)
    board = held(named)
    code, shown = run("list", capsys=capsys)
    assert code == 0 and board in shown and named in shown
    request.getfixturevalue("subagent")
    code, shown = run("remove", board, capsys=capsys)
    assert code == 3 and held(named) == board, "a subagent removes nothing"
    monkeypatch.setenv("POOLHOUSE_WORKSPACE_AGENT", request.getfixturevalue("lead").name)
    code, shown = run("remove", "no-such-project", capsys=capsys)
    assert code == 3 and "could not remove" in shown
    code, shown = run("remove", named, capsys=capsys)
    assert code == 0 and f"removed {board}" in shown and held(named) == ""
    assert run("remove", board, capsys=capsys)[0] == 3, "once is enough: removing it again is refused with the reason"

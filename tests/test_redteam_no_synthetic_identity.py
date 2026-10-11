"""Red team: no agent can obtain an identity for another actor or one that reads as a person.

A real node, real child processes for the command line, no mocks. The node names every session and stamps every write
from the token; a client has no verb that makes an identity for another actor.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from poolhouse.board import grants as board_grants
from poolhouse.board.client import NodeError
from poolhouse.board.names import RESERVED, valid_name
from poolhouse.fleet.onboard.manifest import Signer
from poolhouse.workspace import agent_display
from poolhouse.workspace.cli import COMMANDS

pytest_plugins = ["node_kit"]
pytestmark = pytest.mark.redteam

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "poolhouse"
REGISTERED_BY = {
    "src/poolhouse/api/_node.py": {"register"},
    "src/poolhouse/board/person.py": {"register"},
    "src/poolhouse/board/place.py": {"register"},
    "src/poolhouse/board/public.py": {"register"},
    "src/poolhouse/board/session.py": {"register"},
    "src/poolhouse/harnessid.py": {"register"},
    "src/poolhouse/node_join.py": {"register"},
    "src/poolhouse/node_join_check.py": {"register"},
    "src/poolhouse/workspace/board_cli.py": {"register"},
    "src/poolhouse/workspace/localstart.py": {"register"},
}
"""The reviewed places that ask the node to register a session: each registers the session of the process that asks."""
NAMING = ("sender", "name", "parent", "label", "from", "author", "actor", "identity", "as")
REMOVED_VERBS = ("delegate", "mint", "init", "invite", "join", "connect", "setup", "revoke", "sync", "coordinator", "remote")
IDENTITY_VERBS = {"spawn", "agent", "rebind", "grants-setup"}


def _is_register(node: ast.Call) -> bool:
    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
    if name == "register" and "Native(" in ast.unparse(node):
        return True
    return name == "call" and any(isinstance(arg, ast.Constant) and arg.value == "register" for arg in node.args[:2])


def call_sites() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and _is_register(node):
                found.setdefault(path.relative_to(ROOT).as_posix(), set()).add("register")
    return found


def sessions(node, who):
    return sorted(a.name for a in node.session(who).agents(True))


def test_the_places_that_register_a_session_are_the_reviewed_ones():
    assert call_sites() == REGISTERED_BY


def test_the_command_line_has_no_verb_that_makes_an_identity_for_another_actor():
    verbs = {c.name: getattr(c, "help", "") for c in COMMANDS.commands}
    assert not set(REMOVED_VERBS) & set(verbs)
    risky = {name for name, text in verbs.items()
             if any(word in text.lower() for word in ("mint", "child identity", "delegate", "new identity", "invite"))}
    assert risky <= IDENTITY_VERBS


@pytest.mark.parametrize("verb", REMOVED_VERBS)
def test_a_removed_identity_verb_is_refused_by_the_real_command_and_makes_no_session(workspace_node, verb):
    node = workspace_node
    worker = node.member("worker")
    before = sessions(node, worker)
    done = node.cli(verb, "kid", "--agent", worker.name, who=worker, env={"CLAUDECODE": "1"})
    assert done.returncode != 0
    assert worker.token not in done.stdout + done.stderr
    assert sessions(node, worker) == before


@pytest.mark.parametrize("field", NAMING)
def test_a_registration_that_names_who_it_is_for_is_refused_and_makes_no_session(workspace_node, field):
    node = workspace_node
    worker = node.member("worker")
    before = sessions(node, worker)
    for token in ("", worker.token):
        with pytest.raises(NodeError):
            node.client.call("register", node.board, token, model="claude-sonnet-5-5", harness="claude-code",
                             session="someone-else", **{field: "owner"})
    assert sessions(node, worker) == before


@pytest.mark.parametrize("model", ["human", "owner", "person", "poolhouse-ui", "workspace", "system"])
def test_a_person_looking_model_is_named_by_the_node_as_an_agent_and_never_as_a_person(workspace_node, model):
    node = workspace_node
    made = node.member(f"claim-{model}", model=model, harness="claude-code")
    assert valid_name(made.name) and made.name not in RESERVED and not made.name.startswith(("human", "owner", "person"))
    row = next(a for a in node.session(made).agents() if a.name == made.name)
    shown = agent_display.describe(row)
    assert shown["session_kind"] == "main" and shown["display_name"] == made.name


@pytest.mark.parametrize("name", [*sorted(RESERVED), "Human", "OWNER", "../x", "a b"])
def test_a_reserved_or_malformed_name_is_not_a_session_name(name):
    assert not valid_name(name)


def test_a_subagent_is_a_standard_agent_of_its_parent_and_holds_none_of_its_grants(workspace_node):
    node = workspace_node
    worker = node.member("worker")
    node.allow(worker, "note_verify")
    kid = node.member("kid", parent=worker)
    row = next(a for a in node.session(worker).agents() if a.name == kid.name)
    shown = agent_display.describe(row)
    assert (row.parent, row.model_state, shown["session_kind"]) == (worker.name, "claimed", "subagent")
    assert shown["coordinator_eligible"] is False
    assert [g.action for g in board_grants.listing(node.session(worker)).grants if g.mine] == ["note_verify"]
    assert [g for g in board_grants.listing(node.session(kid)).grants if g.mine] == []


def test_a_second_person_cannot_be_enrolled_from_an_agent_process(workspace_node):
    node = workspace_node
    worker = node.member("worker")
    stranger = Signer.generate()
    proof = board_grants.enrolment(stranger, "an agent that calls itself a person")
    for extra in ({}, {"secret": "a guess"}, {"by": "x", "approval": "x", "approved_ms": 0, "nonce": "x", "anchor": "x"}):
        with pytest.raises(NodeError):
            node.client.call("person_enroll", node.board, worker.token, **proof, **extra)
    assert not board_grants.listing(node.session(worker)).persons
    marked = node.cli("grants-setup", "--session", worker.name, "--json", who=worker, env={"CLAUDECODE": "1"})
    assert marked.returncode == 3 and "agent" in json.loads(marked.stdout)["error"]
    assert not board_grants.listing(node.session(worker)).persons

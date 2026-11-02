"""The person's session is the node's own mark on a session its UI registers, never a harness and model a session says it runs."""

from __future__ import annotations

import pytest

from poolhouse.board import person
from poolhouse.board.client import Denied
from poolhouse.board.session import Native
from poolhouse.workspace import localagent as la

pytest_plugins = ["node_kit", "local_kit"]


def order(sender: str, to: str) -> dict[str, str]:
    return {"from": sender, "to": to, "type": "task", "state": "clear", "raw": "go"}


def test_a_session_that_says_it_is_the_persons_is_not_the_person(team):
    claimant = team.node.member("claimant")
    said = team.node.cli("whoami", "--model", person.MODEL, "--harness", person.HARNESS, "--json", who=claimant)
    assert said.returncode != 0, said.stdout
    mine = team.session(claimant)
    assert la.is_person(mine) is False
    with pytest.raises(Denied):
        mine.whoami(model=person.MODEL, harness=person.HARNESS)
    with pytest.raises(Denied):
        mine.register(Native(person.MODEL, person.HARNESS, "person-claimant"))
    assert la.is_person(mine) is False and la.is_person(team.person) is True
    assert [a.name for a in mine.agents() if a.person] == [team.person.name]


def test_a_worker_obeys_the_persons_session_and_not_a_session_that_claims_to_be_it(team):
    started = team.node.member("started", parent=team.stranger)
    worker = la.Agent(name="local-x", model="m", identity=started.name, board=team.node.board)
    claimant = team.node.member("claimant")
    with pytest.raises(Denied):
        team.session(claimant).whoami(model=person.MODEL, harness=person.HARNESS)
    mine = team.session(started)
    assert la.obeys(mine, worker, order(team.person.name, started.name)) == "the person"
    assert la.obeys(mine, worker, order(claimant.name, started.name)) == ""
    child = team.node.member("claimant-child", parent=claimant)
    assert la.obeys(mine, worker, order(child.name, started.name)) == ""


def test_only_the_person_manages_a_worker_a_session_did_not_start(team):
    worker = team.worker("local-t")
    claimant = team.node.member("claimant")
    with pytest.raises(Denied):
        team.session(claimant).whoami(model=person.MODEL, harness=person.HARNESS)
    assert la.manages(team.person, worker) is True
    assert la.manages(team.session(claimant), worker) is False

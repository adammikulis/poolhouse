"""What the isolation library writes to the activity log: every grant decision and every failed proof, with who asked and never the subject."""

from __future__ import annotations

import json

import pytest

from poolhouse.isolation import Arguments, IsolationFailed, decide_launch, proof, remove_project
from poolhouse.sandbox.seatbelt import Seatbelt
from tests.activity_support import (
    entries,
    person,
    ring,
)
from tests.confined_kit import repository
from tests.isolation_kit import person_home, registered_project, world

__all__ = ["person", "ring"]  # fixtures this module's tests ask for by name
pytest_plugins = ["node_kit"]


def test_every_decision_is_logged_with_its_issuer_and_the_source_it_claimed_and_never_the_subject(tmp_path, monkeypatch, person, workspace_node):
    person_home(tmp_path, monkeypatch)
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    project = registered_project(tmp_path, workspace_node)
    launch = decide_launch(Arguments(allow_net=("evil.example", "pypi.org")), project=project)
    assert [each.granted for each in launch.decisions] == [False, True]
    rows = [row for row in entries() if row.kind == "isolation.grant"]
    assert [row.outcome for row in rows] == ["refused", "granted"]
    assert all(row.meta["issuer"].startswith("user ") and row.meta["source"] == "task" for row in rows)
    assert all(":" in row.subject and "evil.example" not in row.subject and "pypi.org" not in row.subject for row in rows)
    assert rows[0].meta["line"] == launch.decisions[0].line and rows[0].meta["cause"] == "not on the registry list"


def test_a_registered_and_a_removed_project_are_logged_with_the_id_and_who_asked_and_never_the_path(tmp_path, monkeypatch, person, workspace_node):
    person_home(tmp_path, monkeypatch)
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    named = str(repository(tmp_path / "accept-repo").resolve())
    decide_launch(Arguments(), project=named)
    [row] = [row for row in entries() if row.kind == "isolation.project_registered"]
    assert row.outcome == "registered" and row.subject == row.meta["project"] != "" and row.meta["source"] == "working directory"
    assert row.meta["issuer"].startswith("user ") and named not in json.dumps(row.meta) and len(row.meta["place"]) == 16
    assert remove_project(named) == row.subject
    [gone] = [row for row in entries() if row.kind == "isolation.project_removed"]
    assert (gone.outcome, gone.subject) == ("removed", row.subject) and gone.meta["issuer"].startswith("user ")


class Open(Seatbelt):
    """The real backend with the network opened on its way to the kernel."""

    def wrap(self, argv, policy, **kept):
        made = super().wrap(argv, policy, **kept)
        made.argv[2] += "(allow network-outbound)\n"
        return made


def test_a_failed_proof_is_a_record_with_the_attempt_the_guarantee_and_the_digest(tmp_path, person):
    with world(tmp_path) as w, pytest.raises(IsolationFailed):
        proof.run(w.profile, w.services, via=Open(), budget=20.0, lan=False)
    [row] = [row for row in entries() if row.kind == "isolation.proof"]
    assert (row.subject, row.outcome, row.meta["guarantee"], row.meta["digest"]) == ("P07", "failed", "G4", w.profile.digest)

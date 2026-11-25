"""What the isolation library writes to the activity log: every grant decision and every failed proof, with who asked and never the subject."""

from __future__ import annotations

import pytest

from poolhouse.isolation import Arguments, IsolationFailed, decide_launch, proof
from poolhouse.sandbox.seatbelt import Seatbelt
from tests.activity_support import (
    entries,
    person,
    ring,
)
from tests.isolation_kit import person_home, registered_project, world

__all__ = ["person", "ring"]  # fixtures this module's tests ask for by name
pytest_plugins = ["node_kit"]


def test_every_decision_is_logged_with_its_issuer_and_the_source_it_claimed_and_never_the_subject(tmp_path, monkeypatch, person, workspace_node):
    person_home(tmp_path, monkeypatch)
    monkeypatch.setenv("POOLHOUSE_HOME", str(workspace_node.state.parent))
    project = registered_project(tmp_path, workspace_node)
    launch = decide_launch(Arguments(allow_net=("evil.example",), publish=("src/app.py",)), project=project)
    assert [each.granted for each in launch.decisions] == [False, True]
    rows = [row for row in entries() if row.kind == "isolation.grant"]
    assert [row.outcome for row in rows] == ["refused", "granted"]
    assert all(row.meta["issuer"].startswith("user ") and row.meta["source"] == "task" for row in rows)
    assert all(":" in row.subject and "evil.example" not in row.subject and "src/app.py" not in row.subject for row in rows)
    assert rows[0].meta["line"] == launch.decisions[0].line and rows[0].meta["cause"] == "not on the registry list"


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

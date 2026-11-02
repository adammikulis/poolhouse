"""What the node writes to a board, counted with the board tally's labels on a real node: subagents, claims of things on one device
and a claim on a branch."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import board_tally
import pytest

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node):
    workspace_node.lead = workspace_node.member("lead")
    return workspace_node


def rows(team) -> Counter:
    """The rows of the board by the tally's labels."""
    held = team.session(team.lead)
    found = ((e.kind, e.sender, e.fields, e.at_ms) for kind in board_tally.KINDS for e in held.read_all(kind=kind))
    return board_tally.tally(found)[0]


def added(team, work) -> Counter:
    """The rows ``work`` wrote."""
    before = rows(team)
    work()
    return rows(team) - before


def test_ten_subagents_that_start_and_end_write_two_identity_rows_each(team):
    def work():
        for n in range(10):
            kid = team.member(f"kid-{n}", parent=team.lead)
            team.session(kid).retire()

    wrote = added(team, work)
    print("ten subagents:", dict(wrote))
    assert wrote == Counter({"identity registered or updated": 10, "identity retired": 10})


def test_claims_of_things_on_one_device_write_no_row_and_a_branch_claim_writes_two(team):
    held = team.session(team.lead)
    work = Path(team.state).parent / "work"
    wrote = {
        "install": added(team, lambda: (held.claim("install", str(work / "venv"), ttl_s=600), held.release("install", str(work / "venv")))),
        "worktree": added(team, lambda: (held.claim("worktree", str(work / "tree"), ttl_s=600), held.release("worktree", str(work / "tree")))),
        "branch": added(team, lambda: (held.claim("branch", "feat", ttl_s=600), held.release("branch", "feat"))),
    }
    print("claims:", {kind: dict(counted) for kind, counted in wrote.items()})
    assert (wrote["install"], wrote["worktree"]) == (Counter(), Counter())
    assert wrote["branch"] == Counter({"lease branch acquire": 1, "lease branch release": 1})

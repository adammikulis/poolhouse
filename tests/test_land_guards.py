"""A branch that changes the guards, the hook settings or the landing code is left to a person: the runner never lands it."""

from __future__ import annotations

import pytest
from land_world import World, mod

pytest_plugins = ["node_kit"]

GUARDS = {
    "AGENTS.md": "rules\n",
    "CLAUDE.md": "rules\n",
    ".claude/settings.json": "{}\n",
    ".claude/agents/worker.md": "---\n---\n",
    "scripts/hooks/pre-push": "#!/bin/sh\nexit 0\n",
    "scripts/hooks/weakened-assertions": "#!/bin/sh\nexit 0\n",
    "scripts/hooks/claude-bash-guard": "#!/bin/sh\nexit 0\n",
    "src/poolhouse/workspace/person_store.py": "X = 1\n",
    "src/poolhouse/worktreerules.py": "X = 1\n",
    "scripts/land_board.py": "X = 1\n",
    "scripts/github-protection": "X = 1\n",
    ".github/workflows/ci.yml": "on: push\n",
}


@pytest.fixture
def world(monkeypatch, tmp_path, workspace_node):
    return World(monkeypatch, tmp_path, workspace_node)


@pytest.mark.parametrize("path", sorted(GUARDS))
def test_a_request_that_changes_a_guarded_path_waits_for_a_person_and_nothing_lands(world, path):
    rid, _ = world.ready("guards", {**mod(1), path: GUARDS[path]})
    before = world.origin_head()
    world.runner.once()
    assert world.status(rid) == "needs-human" and path in world.detail(rid)
    assert "person" in world.detail(rid)
    assert world.origin_head() == before and world.local_head() == before
    assert any("needs-human" in text and path in text for text in world.texts("alice"))


def test_a_request_that_touches_no_guarded_path_still_lands(world):
    rid, _ = world.ready("plain", {**mod(2), "docs/note.md": "x\n", "scripts/hooks/workspace_hook.py": "X = 1\n"})
    world.runner.once()
    assert world.status(rid) == "landed"


def test_a_guarded_request_does_not_hold_back_the_one_beside_it(world):
    held, _ = world.ready("guards", {**mod(3), "AGENTS.md": "rules\n"})
    free, _ = world.ready("plain", mod(4), who="bob")
    world.runner.once()
    assert (world.status(held), world.status(free)) == ("needs-human", "landed")

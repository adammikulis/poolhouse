"""A runtime deploy writes board rows only for a cutover that happens: install claims are taken for the paths a cutover
changes, after the build, the smoke and the boundary have all succeeded; a deploy that finds nothing to do writes nothing."""

from __future__ import annotations

import pytest
from test_runtime_deploy import agent, builder, commit, plan_for, world  # noqa: F401

from poolhouse import runtime, runtime_deploy, runtime_git, runtime_store

pytestmark = pytest.mark.slow
pytest_plugins = ["node_kit"]
KINDS = ("message", "lease", "audit", "identity", "landing", "note")


def rows(me) -> dict[str, int]:
    """How many rows of each kind the board holds, read through the node."""
    return {kind: len(me.read_all(kind=kind)) for kind in KINDS}


def claimed_paths(monkeypatch) -> list:
    """The paths every install claim of a deploy asks for, in order."""
    seen: list = []
    real = runtime_deploy.owning

    def spy(paths, **kwargs):
        seen.append(list(paths))
        return real(paths, **kwargs)

    monkeypatch.setattr(runtime_deploy, "owning", spy)
    return seen


def test_an_ensure_with_nothing_to_do_writes_no_row(world, workspace_node, agent):  # noqa: F811
    repo, launchers, _ = world
    commit(repo, "a")
    me = workspace_node.session(agent)
    assert runtime_deploy.ensure(plan_for(repo, launchers), builder=builder()).action == "switched"
    before = rows(me)
    for _ in range(3):
        assert runtime_deploy.ensure(plan_for(repo, launchers), builder=builder()).action == "current"
    assert rows(me) == before


def test_a_cutover_that_finds_the_boundary_held_writes_no_row_and_the_next_one_claims_once(world, workspace_node, agent, monkeypatch):  # noqa: F811
    repo, launchers, _ = world
    commit(repo, "a")
    me = workspace_node.session(agent)
    runtime_deploy.ensure(plan_for(repo, launchers), builder=builder())
    commit(repo, "b")
    before = rows(me)
    with runtime_git.crossing(repo, note="a landing"):
        for _ in range(3):
            assert runtime_deploy.ensure(plan_for(repo, launchers), builder=builder()).action == "busy"
    assert rows(me) == before
    asked = claimed_paths(monkeypatch)
    assert runtime_deploy.ensure(plan_for(repo, launchers), builder=builder()).action == "switched"
    after = rows(me)
    assert len(asked) == 1 and after["lease"] - before["lease"] == 2 * len(asked[0])
    assert {kind: n - before[kind] for kind, n in after.items() if kind != "lease"} == {kind: 0 for kind in KINDS if kind != "lease"}


def test_a_build_that_fails_its_smoke_writes_no_row(world, workspace_node, agent):  # noqa: F811
    repo, launchers, _ = world
    commit(repo, "a")
    me = workspace_node.session(agent)
    runtime_deploy.ensure(plan_for(repo, launchers), builder=builder())
    commit(repo, "b")
    before = rows(me)
    assert runtime_deploy.ensure(plan_for(repo, launchers), builder=builder(hook_code=3)).action == "failed"
    assert rows(me) == before


def test_the_install_claims_cover_the_paths_the_cutover_changes_and_not_the_new_tree(world, agent, monkeypatch):  # noqa: F811
    repo, launchers, _ = world
    head = commit(repo, "a")
    asked = claimed_paths(monkeypatch)
    assert runtime_deploy.ensure(plan_for(repo, launchers), builder=builder()).action == "switched"
    [paths] = asked
    assert launchers in paths and runtime.directory() / "selected.json" in paths
    assert not [path for path in paths if (runtime.directory() / head) in path.parents]
    assert runtime_store.selection()["commit"] == head

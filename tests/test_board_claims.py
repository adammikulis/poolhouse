"""Claims on the node as the commands use them: one winner among racing processes, the keys a claim is made under, how long one lasts."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from poolhouse.board.client import Conflict

pytest_plugins = ["node_kit"]


@pytest.fixture
def team(workspace_node):
    node = workspace_node
    node.alpha, node.beta = node.member("alpha"), node.member("beta")
    return node


def run(node, who, *argv):
    done = node.cli(*argv, "--json", who=who)
    assert done.returncode == 0, (argv, done.stderr, done.stdout)
    return json.loads(done.stdout)


def test_processes_racing_for_a_port_have_exactly_one_winner(team):
    racers = [team.member(f"racer-{n}") for n in range(4)]
    procs = [subprocess.Popen([sys.executable, "-m", "poolhouse.workspace.cli", "claim", "port", "9200"], env=team.env(who),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for who in racers]
    assert sorted(p.wait(timeout=120) for p in procs) == [0, 5, 5, 5]
    owner = run(team, team.alpha, "who", "port", "9200")["owner"]
    assert owner in {who.name for who in racers}


@pytest.mark.parametrize(("kind", "key"), [("port", "0"), ("port", "70000"), ("port", "80x"), ("port", ""), ("worktree", "relative/tree"),
                                           ("install", "venv"), ("area", "not/absolute"), ("planet", "mars"),
                                           ("branch", "../x"), ("server", "")])
def test_bad_claim_keys_are_refused(team, kind, key):
    done = team.cli("claim", kind, key, who=team.alpha)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert run(team, team.alpha, "claims") == []


def test_a_path_claimed_under_another_spelling_is_the_same_claim(team, tmp_path):
    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(tree, target_is_directory=True)
    assert team.cli("claim", "worktree", str(tree), who=team.alpha).returncode == 0
    assert team.cli("claim", "worktree", str(alias), who=team.beta).returncode == 5
    assert team.cli("claim", "worktree", str(alias / "src"), who=team.beta).returncode == 5
    assert team.cli("claim", "worktree", str(tmp_path), who=team.beta).returncode == 5
    assert team.cli("claim", "worktree", str(tmp_path / "other"), who=team.beta).returncode == 0
    assert run(team, team.beta, "who", "worktree", str(alias))["owner"] == team.alpha.name


def test_a_listing_flags_a_claim_about_to_expire_and_a_heartbeat_renews_only_the_callers(team):
    run(team, team.alpha, "claim", "branch", "agent/x", "--ttl", "100")
    run(team, team.alpha, "claim", "server", "api", "--ttl", "100")
    run(team, team.beta, "claim", "branch", "agent/y", "--ttl", "100")
    run(team, team.beta, "claim", "port", "9201")
    rows = {(c["kind"], c["key"]): c for c in run(team, team.alpha, "claims")}
    assert rows[("branch", "agent/x")]["expiring_soon"] is True and rows[("branch", "agent/x")]["expires_in_s"] <= 100
    assert rows[("port", "9201")]["expiring_soon"] is False and rows[("port", "9201")]["expires_in_s"] > 600
    assert run(team, team.alpha, "heartbeat", "--ttl", "3000")["renewed"] == 2
    rows = {(c["kind"], c["key"]): c for c in run(team, team.alpha, "claims")}
    assert all(rows[key]["expires_in_s"] > 2900 and rows[key]["expiring_soon"] is False for key in (("branch", "agent/x"), ("server", "api")))
    assert rows[("branch", "agent/y")]["expiring_soon"] is True and rows[("branch", "agent/y")]["expires_in_s"] <= 100


def test_a_claim_taken_by_another_names_its_owner_and_is_listed_by_the_kind(team):
    run(team, team.alpha, "claim", "branch", "agent/z")
    taken = team.cli("claim", "branch", "agent/z", "--json", who=team.beta)
    assert taken.returncode == 5 and team.alpha.name in json.loads(taken.stdout)["error"]
    assert [c["key"] for c in run(team, team.beta, "claims", "--kind", "branch")] == ["agent/z"]
    assert run(team, team.beta, "claims", "--kind", "port") == []
    assert run(team, team.beta, "claims", "--for-agent", team.alpha.name)[0]["owner"] == team.alpha.name


def test_areas_held_under_a_path_keep_another_session_from_a_worktree_or_install_around_it(team, tmp_path):
    a, b = team.session(team.alpha), team.session(team.beta)
    tree = tmp_path / "repo" / "x"
    a.reserve([("area", str(tree / "src" / "a.py")), ("area", str(tree / "docs" / "b.md"))])
    for kind, key in [("worktree", tree), ("worktree", tree / "src"), ("install", tree), ("area", tree / "src")]:
        with pytest.raises(Conflict, match=team.alpha.name):
            b.claim(kind, str(key))
    with pytest.raises(Conflict, match=team.alpha.name):
        b.reserve([("port", "9400"), ("worktree", str(tree))])
    assert [c for c in b.claims() if c.owner == team.beta.name] == [], "a refused reserve takes none of its set"
    b.claim("worktree", str(tmp_path / "repo" / "xy"))
    b.claim("worktree", str(tmp_path / "repo" / "y"))
    a.claim("worktree", str(tmp_path / "work"))
    with pytest.raises(Conflict, match=team.alpha.name):
        b.reserve([("area", str(tmp_path / "work" / "src" / "m.py"))])


def test_a_heartbeat_names_the_claims_whose_eight_hour_lifetime_held_the_renewal_back(team):
    run(team, team.alpha, "claim", "port", "9500")
    run(team, team.alpha, "claim", "server", "api")
    assert run(team, team.alpha, "heartbeat", "--ttl", "600") == {"renewed": 2, "capped": []}
    done = run(team, team.alpha, "heartbeat", "--ttl", "86400")
    assert done["renewed"] == 2 and sorted(done["capped"]) == ["port:9500", "server:api"]
    assert all(28_700 < c["expires_in_s"] <= 28_800 for c in run(team, team.alpha, "claims")), "renewed to the lifetime and no further"

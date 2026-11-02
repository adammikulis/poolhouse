"""Cleanup of an ended claim is idempotent without surrendering another session's ownership."""

import json
import time
from argparse import Namespace

import pytest

from poolhouse.board.client import NodeError
from poolhouse.workspace import claims_cli

pytest_plugins = ["node_kit"]


def release(node, who, key="feature", *extra):
    return node.cli("release", "branch", key, *extra, "--json", who=who)


def test_releasing_twice_or_never_held_succeeds_without_claiming(workspace_node):
    node = workspace_node
    actor = node.member("worker")
    node.session(actor).claim("branch", "feature")
    first, second = release(node, actor), release(node, actor)
    assert first.returncode == second.returncode == 0, (first.stdout, second.stdout)
    assert json.loads(first.stdout) == {"kind": "branch", "key": "feature", "released": True}
    assert json.loads(second.stdout) == {"kind": "branch", "key": "feature", "released": False}
    never = release(node, actor, "never-held")
    assert never.returncode == 0, never.stdout
    assert json.loads(never.stdout)["released"] is False
    assert node.session(actor).claims() == []


def test_releasing_expired_claim_succeeds_without_reacquiring(workspace_node):
    node = workspace_node
    actor = node.member("worker")
    node.session(actor).claim("branch", "feature", ttl_s=1)
    time.sleep(1.1)
    done = release(node, actor)
    assert done.returncode == 0, done.stdout
    assert json.loads(done.stdout)["released"] is False
    assert node.session(actor).claims() == []


def test_cleanup_never_releases_another_live_or_reacquired_claim(workspace_node):
    node = workspace_node
    actor, other = node.member("worker"), node.member("other")
    node.session(actor).claim("branch", "feature")
    assert release(node, actor).returncode == 0
    node.session(other).claim("branch", "feature")
    refused = release(node, actor)
    assert refused.returncode != 0 and other.name in refused.stdout
    assert [(claim.owner, claim.key) for claim in node.session(other).claims()] == [(other.name, "feature")]


def test_cleanup_cannot_name_a_parent_or_unrelated_holder(workspace_node):
    node = workspace_node
    parent = node.member("parent")
    child = node.member("child", parent=parent)
    unrelated = node.member("unrelated")
    for actor, target in ((child, parent), (parent, unrelated)):
        done = release(node, actor, "never-held", "--held-by", target.name)
        assert done.returncode != 0 and "only a parent" in done.stdout


@pytest.mark.parametrize(("kind", "key"), [
    ("port", "0"), ("area", "relative"), ("branch", "../x"), ("planet", "x"),
    ("branch", ""), ("branch", "x" * 301), ("branch", "line\nbreak"),
])
def test_invalid_release_is_still_refused(workspace_node, kind, key):
    node = workspace_node
    done = node.cli("release", kind, key, "--json", who=node.member("worker"))
    assert done.returncode != 0


def test_cleanup_does_not_hide_an_unreachable_node(workspace_node):
    node = workspace_node
    actor = node.member("worker")
    node.client.autostart = False
    node.stop()
    with pytest.raises((OSError, NodeError)):
        claims_cli.release(Namespace(kind="branch", key="feature", held_by=""), node.session(actor))

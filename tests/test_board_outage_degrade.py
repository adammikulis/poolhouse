"""A board that said no still says no, and a node that cannot be reached is an error, not a silent success."""

import argparse
import json

import pytest

from poolhouse import node_binary
from poolhouse.board.client import Conflict, Invalid, NodeError, Quota
from poolhouse.errors import Denied
from poolhouse.node_launch import NodeUnavailable
from poolhouse.workspace import board_cli


def _run(cmd, error, *, as_json=False):
    def run(_args):
        raise error
    return board_cli.guarded(run)(argparse.Namespace(cmd=cmd, json=as_json))


@pytest.mark.parametrize("error", [
    NodeUnavailable("the node did not answer within 20s"),
    ConnectionRefusedError("connection refused"),
    node_binary.NodeBinaryError("no runtime is selected"),
])
def test_an_unreachable_node_fails_every_command(error, capsys):
    for cmd in ("send", "announce", "release", "claim"):
        assert _run(cmd, error) == 3
    err = capsys.readouterr().err
    assert "was not recorded" not in err and str(error) in err


def test_real_refusals_keep_their_own_status_and_say_no(capsys):
    assert _run("announce", Denied("this token may not announce")) == 3
    assert _run("announce", NodeError("denied", "the node refused")) == 3
    assert _run("announce", Invalid("invalid", "one line of at most 200 characters")) == 2
    assert _run("claim", Quota("quota", "a session holds at most 64 claims")) == 4
    assert _run("claim", Conflict("denied", "held by claude-0a1b2c")) == 5
    assert "was not recorded" not in capsys.readouterr().err


def test_a_failure_is_one_json_object_when_json_is_asked(capsys):
    assert _run("send", NodeUnavailable("the node is down"), as_json=True) == 3
    shown = json.loads(capsys.readouterr().out)
    assert shown["code"] == 3 and shown["kind"] == "NodeUnavailable" and "node is down" in shown["error"]

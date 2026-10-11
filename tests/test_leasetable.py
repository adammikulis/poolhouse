"""The lease table of the node, as the serve broker and the request gate reach it: one shared session, a lease per tag, the node started when it is not running."""

from __future__ import annotations

import json

import pytest

from poolhouse import leasetable
from poolhouse.node_health import Refused

pytest_plugins = ["node_kit"]

MIB = 1 << 20


@pytest.fixture
def table(workspace_node):
    return leasetable.Table(workspace_node.state)


def test_every_table_of_a_state_directory_is_the_same_session(table, workspace_node):
    other = leasetable.Table(workspace_node.state)
    first = table.acquire([leasetable.gpu()], leasetable.Terms(tag="a"))
    assert first["state"] == "held"
    assert other.acquire([leasetable.gpu()], leasetable.Terms(tag="b"))["state"] == "queued", "one session holds a lease per tag, and each waits for the others"
    tokens = sorted((workspace_node.state / "client" / leasetable.BOARD).glob("*.token"))
    assert len(tokens) == 1
    assert {row["holder"] for row in table.listing()["leases"]} == {f"{leasetable.BOARD}/{tokens[0].stem}"}


def test_a_lease_asked_for_again_with_its_tag_is_the_one_already_held(table):
    first = table.acquire([leasetable.memory(10)], leasetable.Terms(tag="server.9000"))
    again = table.acquire([leasetable.memory(10)], leasetable.Terms(tag="server.9000"))
    assert first["id"] == again["id"]
    assert len(table.listing()["leases"]) == 1


def test_a_lease_that_is_given_back_is_gone_and_one_that_never_was_is_not_an_error(table):
    held = table.acquire([leasetable.gpu()], leasetable.Terms(tag="a"))
    assert table.release(held["id"]) is True
    assert table.release(held["id"]) is False
    assert table.listing()["leases"] == []


def test_the_budget_is_written_to_the_nodes_config_and_the_node_keeps_to_it(table, workspace_node):
    path = workspace_node.state / "lease-config.json"
    path.write_text(json.dumps({"max_wait_s": 300}))
    table.budget(1000)
    assert json.loads(path.read_text()) == {"max_wait_s": 300, "memory_mb": 1000}
    assert table.listing()["config"]["memory_mb"] == 1000
    with pytest.raises(Refused) as over:
        table.acquire([leasetable.memory(1500)], leasetable.Terms(tag="big"))
    assert over.value.code == "quota"
    taken = table.acquire([leasetable.memory(700)], leasetable.Terms(tag="a"))
    assert taken["state"] == "held"
    assert table.acquire([leasetable.memory(700)], leasetable.Terms(tag="b"))["state"] == "queued"
    table.budget(1000)
    assert json.loads(path.read_text())["memory_mb"] == 1000


def test_a_lease_bound_to_a_process_ends_with_it(table):
    import subprocess
    import sys

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        held = table.acquire([leasetable.slot("m.gguf", context=512, parallel=1)], leasetable.Terms(tag="server.9000"))
        bound = table.renew(held["id"], pid=child.pid)
        assert bound["pid"] == child.pid
    finally:
        child.kill()
        child.wait(timeout=10)
    assert table.listing()["leases"] == []


def test_the_node_is_started_when_it_does_not_answer(table, workspace_node):
    workspace_node.stop()
    held = leasetable.Table(workspace_node.state).acquire([leasetable.gpu()], leasetable.Terms(tag="after-stop"))
    assert held["state"] == "held"
    assert [row["id"] for row in table.listing()["leases"]] == [held["id"]]


def test_a_token_the_node_no_longer_knows_is_replaced(table, workspace_node):
    table.acquire([leasetable.gpu()], leasetable.Terms(tag="a"))
    table._token = "not-a-token"
    assert table.listing()["leases"], "the stale token is dropped and the kept one is read again"

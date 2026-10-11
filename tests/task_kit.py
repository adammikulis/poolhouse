"""A board on a real node with a lead, a worker that holds its model, and an independent session, and the task flows between them."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from poolhouse import node_launch
from poolhouse.board.tasks import Tasks

MODEL = "qwen"


def model_lease(node, who, model=MODEL):
    """The lease of ``model`` that ``who`` holds on this device."""
    return node.session(who).call("lease_acquire", resources=[{"type": "model_slot", "model": model}], ttl_s=5000)["id"]


@pytest.fixture
def board(workspace_node, monkeypatch):
    """The team, its tasks of one spec, and an allocation of the worker's model for the first."""
    monkeypatch.setattr(node_launch, "default_state", lambda: workspace_node.state)
    node = workspace_node
    kit = SimpleNamespace(node=node, lead=node.member("lead"), other=node.member("other"))
    node.allow(kit.lead, "task_authority")
    kit.worker = node.member("worker", parent=kit.lead)
    kit.lease = model_lease(node, kit.worker)
    kit.tasks = lambda who: Tasks(node.session(who))
    kit.spec = {"title": "Inspect native simulation", "description": "Collect a reproducible replay.",
                "acceptance": ["Replay passes"], "source_key": "repo:demo/sim:issue:42"}
    kit.task = kit.tasks(kit.lead).create(kit.spec)
    kit.allocation = kit.tasks(kit.lead).assign(kit.task["id"], kit.worker.name, kit.lease, ["coding"])
    return kit


def proposed(kit, ident=None, allocation=None):
    """The worker claims the task and submits a proposal; the proposal."""
    ident = ident or kit.task["id"]
    work = kit.tasks(kit.worker)
    work.claim(ident, (allocation or kit.allocation)["allocation_id"])
    return work.submit(ident, {"artifacts": {"replay.json": "a" * 64},
                               "checks": [{"name": "Worker claims tests", "passed": True}],
                               "summary": "Native replay proposed",
                               "provenance": {"commit": "abc123", "environment": "native-python",
                                              "model": "qwen", "runtime": "Agents SDK"}})


def accepted():
    return {"accepted": True, "reason": "Independent native replay passed",
            "checks": [{"name": "Replay passes", "passed": True}]}

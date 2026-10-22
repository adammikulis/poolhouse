"""Read-only task admission on actual isolated nodes."""

import pytest
from native_cargo import native_node_units  # noqa: F401
from node_kit import node_binary, workspace_node  # noqa: F401
from task_kit import board

from poolhouse.board.client import NodeError

__all__ = ["board"]


def test_native_task_admission_boundaries(native_node_units):  # noqa: F811
    native_node_units.run_targets(("tasks",))


def test_python_admission_uses_canonical_task_and_current_worker(board):
    worker = board.tasks(board.worker)
    entry = board.task["entry"]
    with pytest.raises(NodeError):
        worker.admit(entry)
    worker.claim(entry, board.allocation["allocation_id"])
    first = worker.admit(entry)
    second = worker.admit(entry)
    assert first["lease"]["deadline"] == second["lease"]["deadline"]
    assert first["entry"] == entry and first["worker"] == board.worker.name
    assert first["lease"]["active"]
    for identity in (board.lead, board.other):
        with pytest.raises(NodeError):
            board.tasks(identity).admit(entry)
    for invalid in (board.task["id"], "", None, 42, "../task"):
        with pytest.raises(ValueError, match="canonical"):
            worker.admit(invalid)
    board.node.session(board.worker).call("lease_release", id=board.lease)
    with pytest.raises(NodeError):
        worker.admit(entry)

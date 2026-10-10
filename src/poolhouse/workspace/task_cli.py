"""The task commands of `poolhouse-workspace`: the board's tasks, their leases, outcomes and credit, on the node."""

from __future__ import annotations

import argparse
import json
from typing import Any

from poolhouse.board.tasks import Tasks
from poolhouse.command import flag
from poolhouse.workspace import enforcement_cli, render, task_outcomes
from poolhouse.workspace.cli_options import FOR_AGENT

__all__ = ["TABLE"]


def _payload(args: argparse.Namespace) -> Any:
    return json.loads(render.body(args.payload))


TABLE = (
    ("task-create", "create a task of this board (JSON or - for stdin)", [flag("payload")],
     lambda a, s: Tasks(s).create(_payload(a))),
    ("tasks", "the board's tasks and progress metrics", [FOR_AGENT], lambda a, s: Tasks(s).list()),
    ("task", "task lease, checkpoints, proposal and independent review", [flag("id")], lambda a, s: Tasks(s).get(a.id)),
    ("task-subscribe", "receive inbox status changes for a task", [flag("id")], lambda a, s: Tasks(s).subscribe(a.id)),
    ("task-unsubscribe", "stop inbox status changes for a task", [flag("id")], lambda a, s: Tasks(s).unsubscribe(a.id)),
    ("task-assign", "give a worker the task on the model lease it holds; the worker then claims the allocation printed",
     [flag("id"), flag("worker"), flag("lease", help="the id of the worker's lease of its model, from `lease_list`"),
      flag("--capability", dest="capabilities", action="append", default=[], metavar="NAME",
           help="a capability the allocation provides (repeat for each)")],
     lambda a, s: Tasks(s).assign(a.id, a.worker, a.lease, a.capabilities)),
    ("task-claim", "claim a queued task on an allocation made to you", [flag("id"), flag("allocation_id")],
     lambda a, s: Tasks(s).claim(a.id, a.allocation_id)),
    ("task-heartbeat", "renew your working task's lease", [flag("id")], lambda a, s: Tasks(s).heartbeat(a.id)),
    ("task-checkpoint", "save your working task's checkpoint (JSON or - for stdin)", [flag("id"), flag("payload")],
     lambda a, s: Tasks(s).checkpoint(a.id, _payload(a))),
    ("task-submit", "submit immutable artifact hashes for review (JSON or -)", [flag("id"), flag("payload")],
     lambda a, s: Tasks(s).submit(a.id, _payload(a))),
    ("task-review", "independently review a task (JSON or -)", [flag("id"), flag("payload")],
     lambda a, s: task_outcomes.seat_review(s, a.id, _payload(a))),
    ("enforcement", "show, check, set, promote or demote the board's task enforcement mode (open or strict)",
     enforcement_cli.OPTIONS, enforcement_cli.run),
    ("task-credit", "retry recording the credit of an independently reviewed outcome", [flag("id")],
     lambda a, s: task_outcomes.seat_credit(s, a.id)),
)

"""The `enforcement` command of `poolhouse-workspace`."""

from __future__ import annotations

import argparse
from typing import Any

from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.command import flag
from poolhouse.workspace import enforcement_check

ACTIONS = ('show', 'check', 'set', 'promote', 'demote')
OPTIONS = [
    flag('action', nargs='?', choices=ACTIONS, default='show',
         help='show the mode, check what strict would strand, set open|strict, promote or demote'),
    flag('value', nargs='?', default='', help='open or strict, for set'),
]


def run(args: argparse.Namespace, s: Session) -> Any:
    """Show, check or change the task enforcement mode of the board."""
    new = {'promote': 'strict', 'demote': 'open'}.get(args.action) or args.value
    if args.action in ('promote', 'demote', 'set'):
        report = enforcement_check.check(s) if new == 'strict' else None
        return {**Tasks(s).set_mode(new), **({'preflight': report} if report else {})}
    if args.action == 'check':
        return enforcement_check.check(s)
    return Tasks(s).mode()

"""The `authority` command of `poolhouse-workspace`: show, set and preset the delegable gates."""

from __future__ import annotations

import argparse
import contextlib
from typing import Any

from poolhouse import authority
from poolhouse.board.client import NodeError
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.command import flag
from poolhouse.errors import Denied
from poolhouse.workspace import enforcement_check

ACTIONS = ('show', 'set', 'preset')
OPTIONS = [
    flag('action', choices=ACTIONS, help='show the gates, set some to person or delegated, or apply the dev or prod preset'),
    flag('words', nargs='*', default=[],
         help='set: person|delegated then ALL, a group or gates; preset: dev|prod'),
    flag('--project', default='', help='a project key: set its own state; preset: the project whose state follows'),
]


def _flip(who: str, gates: list[str], new: str, where: tuple[str, str]) -> list[dict[str, str]]:
    scope, preset = where
    return authority.set_state(gates, new, by=who, scope=authority.Scope(scope, preset))


def _preset(s: Session, name: str, scope: str) -> dict[str, Any]:
    if name not in authority.PRESETS:
        raise ValueError(f'preset must be one of {", ".join(authority.PRESETS)}')
    keep = authority.PROD_DELEGATED if name == 'prod' else set(authority.GATES)
    changes = [*_flip(s.name, sorted(keep), authority.DELEGATED, (scope, name)),
               *_flip(s.name, sorted(set(authority.GATES) - keep), authority.PERSON, (scope, name))]
    mode = 'strict' if name == 'prod' else 'open'
    report = enforcement_check.check(s) if mode == 'strict' else None
    return {'preset': name, 'changes': [c for c in changes if c['from'] != c['to']],
            'enforcement': Tasks(s).set_mode(mode), **({'preflight': report} if report else {})}


def _refused(s: Session, args: argparse.Namespace) -> None:
    """Tell the board a helper asked to change authority."""
    with contextlib.suppress(NodeError, OSError):
        s.audit('authority.set_denied', ' '.join([args.action, *args.words]), 'a helper may not change authority')


def run(args: argparse.Namespace, s: Session) -> Any:
    """Show the registry, or change it as a lead or a person; a subagent is refused."""
    if args.action == 'show':
        return authority.show(args.project)
    if next((a.parent for a in s.agents() if a.name == s.name), ''):
        _refused(s, args)
        raise Denied('only a lead or a person changes authority, not a helper')
    if args.action == 'preset':
        if len(args.words) != 1:
            raise ValueError('preset takes dev or prod')
        return _preset(s, args.words[0], args.project)
    if len(args.words) < 2 or args.words[0] not in (authority.PERSON, authority.DELEGATED):
        raise ValueError('set takes person or delegated, then ALL, a group or gates')
    try:
        gates = authority.resolve(args.words[1:])
    except KeyError as err:
        raise ValueError(str(err.args[0])) from err
    return {'changes': _flip(s.name, gates, args.words[0], (args.project, ''))}

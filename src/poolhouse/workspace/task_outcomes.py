"""Independent task reviews and idempotent outcome credit recording."""

from poolhouse.board import client
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.memory import vault
from poolhouse.workspace import task_credit
from poolhouse.workspace.identity import Denied
from poolhouse.workspace.taskboard import TaskBoard


def _recorded(verify):
    """What recording the credit of one outcome did, as the reviewer is told."""
    try:
        result = verify()
        total = result.get('award', {}).get('total', 0) if result.get('outcome') == 'accepted' else 0
        return {'state': 'recorded', 'credited': result['credited'], 'total': total,
                'evidence_id': result['id']}
    except (Denied, client.Denied):
        return {'state': 'blocked', 'reason': 'This reviewer cannot record credit for this outcome.'}
    except (vault.KeyUnavailable, OSError, RuntimeError):
        return {'state': 'pending', 'reason': 'Credit recording is unavailable. The independent review remains saved.'}


def review(ws, token, ident, decision):
    result = TaskBoard(ws).review(token, ident, decision)
    return {**result, 'credit': credit(ws, token, ident)}


def credit(ws, token, ident):
    return _recorded(lambda: task_credit.verify_task(ws, token, ident))


def session_review(s: Session, ident, decision):
    """Review a task of the session's board and record its credit."""
    result = Tasks(s).review(ident, decision)
    return {**result, 'credit': session_credit(s, ident)}


def session_credit(s: Session, ident):
    """Record the credit of a reviewed task of the session's board."""
    return _recorded(lambda: task_credit.verify_on_board(s, ident))

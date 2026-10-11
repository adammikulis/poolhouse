"""Credit task outcomes using their independently authenticated review."""

from poolhouse import activity
from poolhouse.board import client, taskspec
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.reputation import economy
from poolhouse.workspace import family_accounts, work_reputation


def _denied(why: str) -> client.Denied:
    return client.Denied('denied', why)


def award(task, reviewer, scope, *, ledger=None):
    """Record the independent review of ``task`` by ``reviewer`` in ledger ``scope``, crediting an accepted one once."""
    proposal, review = task.get('proposal'), task.get('review')
    if not proposal or not review:
        raise _denied('a task needs an independently authenticated review')
    outcome = review['outcome']
    if outcome not in ('accepted', 'rejected', 'blocked_infrastructure') \
            or (outcome == 'accepted') != review['accepted']:
        raise _denied('the review outcome is inconsistent')
    if outcome == 'accepted' and task['state'] != 'completed':
        raise _denied('only a completed task earns completion credit')
    worker = proposal['worker']
    if reviewer == worker or review['verifier'] != reviewer:
        raise _denied('only the authenticated independent reviewer can award this task')
    if review['task'] != task['id'] or proposal['task'] != task['id'] \
            or review['worker'] != worker or review['proposal_id'] != proposal['id'] \
            or review['proposal_hash'] != proposal['proposal_hash']:
        raise _denied('the accepted review must bind this exact worker proposal')
    checks = taskspec.checks(review['checks'])
    if outcome == 'accepted' and not all(item['passed'] for item in checks):
        raise _denied('accepted task credit requires independently passing review checks')
    proof = {'checks': checks, 'artifacts': proposal['artifacts'],
             **{key: review[key] for key in ('quality', 'review') if key in review}}
    if review.get('verified_usage'):
        measured = review['verified_usage']
        proof['usage'] = {'source': measured.get('source', 'Independent task review'),
                          **{key: measured.get(key) for key in ('tokens_in', 'tokens_out', 'wall_seconds')}}
    if outcome != 'accepted' and proof.get('quality'):
        raise _denied('an unaccepted contribution cannot earn quality bonuses')
    evidence = {**proof, 'award': economy.assessment(proof)}
    account = proposal.get('family_account') or family_accounts.binding(scope, proposal.get('provenance', {}))
    if account:
        evidence['family_account'] = account
    if proposal.get('provenance'):
        evidence['provenance'] = proposal['provenance']
    data = {**evidence, 'agent': worker, 'verifier': reviewer,
            'task': task['id'], 'completion': review['id'], 'outcome': outcome,
            'reason': review['reason'], 'workspace': scope,
            'verified_at': review['reviewed_at'],
            'task_hash': task.get('spec_hash') or taskspec.fingerprint({key: task[key] for key in taskspec.BOARD_FIELDS}),
            'completion_hash': review['review_hash'],
            'proposal_id': proposal['id'], 'proposal_hash': proposal['proposal_hash'],
            'source': 'taskboard'}
    with work_reputation._store(ledger) as held:
        contribution = held.record_contribution({key: value for key, value in data.items() if key != 'award'})
        result = held.record(data) if outcome == 'accepted' else {**contribution, 'credited': False}
    if result['credited']:
        activity.record('agent.work_verified', actor=worker, subject=task['title'], outcome='verified',
                        refs={'task': task['id'], 'evidence': result['id'], 'verifier': reviewer},
                        meta={'credits': result['award']['total']})
    return result


def verify_on_board(s: Session, task_id, *, ledger=None):
    """Award an accepted task of the session's board once."""
    return award(Tasks(s).get(task_id), s.name, s.board, ledger=ledger)

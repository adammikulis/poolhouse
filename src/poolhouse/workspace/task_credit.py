"""Credit task outcomes using their independently authenticated review."""

from poolhouse import activity
from poolhouse.board import client, taskspec
from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.reputation import economy
from poolhouse.workspace import family_accounts, work_reputation
from poolhouse.workspace.identity import Denied
from poolhouse.workspace.taskboard import TaskBoard


def award(task, reviewer, scope, *, ledger=None):
    """Record the independent review of ``task`` by ``reviewer`` in ledger ``scope``, crediting an accepted one once."""
    proposal, review = task.get('proposal'), task.get('review')
    if not proposal or not review:
        raise Denied('a task needs an independently authenticated review')
    outcome = review['outcome']
    if outcome not in ('accepted', 'rejected', 'blocked_infrastructure') \
            or (outcome == 'accepted') != review['accepted']:
        raise Denied('the review outcome is inconsistent')
    if outcome == 'accepted' and task['state'] != 'completed':
        raise Denied('only a completed task earns completion credit')
    worker = proposal['worker']
    if reviewer == worker or review['verifier'] != reviewer:
        raise Denied('only the authenticated independent reviewer can award this task')
    if review['task'] != task['id'] or proposal['task'] != task['id'] \
            or review['worker'] != worker or review['proposal_id'] != proposal['id'] \
            or review['proposal_hash'] != proposal['proposal_hash']:
        raise Denied('the accepted review must bind this exact worker proposal')
    checks = taskspec.checks(review['checks'])
    if outcome == 'accepted' and not all(item['passed'] for item in checks):
        raise Denied('accepted task credit requires independently passing review checks')
    proof = {'checks': checks, 'artifacts': proposal['artifacts'],
             **{key: review[key] for key in ('quality', 'review') if key in review}}
    if review.get('verified_usage'):
        measured = review['verified_usage']
        proof['usage'] = {'source': measured.get('source', 'Independent task review'),
                          **{key: measured.get(key) for key in ('tokens_in', 'tokens_out', 'wall_seconds')}}
    if outcome != 'accepted' and proof.get('quality'):
        raise Denied('an unaccepted contribution cannot earn quality bonuses')
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


def verify_task(ws, token, task_id, *, ledger=None):
    """Award an accepted task of the workspace's own store once; worker claims never supply verification checks."""
    who = ws.auth(token)
    board = TaskBoard(ws)
    board.assert_reviewer(token, task_id)
    task = board.get(token, task_id)
    if task['workspace'] != work_reputation.scope(ws):
        raise Denied('the task belongs to a different coordinator workspace')
    proposal, review = task.get('proposal'), task.get('review')
    if proposal and review:
        if taskspec.fingerprint({key: value for key, value in proposal.items() if key != 'proposal_hash'}) != proposal['proposal_hash']:
            raise Denied('the proposal content does not match its recorded hash')
        if taskspec.fingerprint({key: value for key, value in review.items() if key != 'review_hash'}) != review['review_hash']:
            raise Denied('the independent review content does not match its recorded hash')
    result = award(task, who.id, work_reputation.scope(ws), ledger=ledger)
    if result['credited']:
        ws.audit('work.verified', who.id, agent=result['agent'], task=task['id'], evidence=result['id'])
    return result


def verify_on_board(s: Session, task_id, *, ledger=None):
    """Award an accepted task of the session's board once."""
    try:
        return award(Tasks(s).get(task_id), s.name, s.board, ledger=ledger)
    except Denied as why:
        raise client.Denied('denied', str(why)) from None

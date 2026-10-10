"""Credits on the board require independent review evidence and a stable board scope."""

import hashlib

import pytest
from task_kit import accepted, board as _board_fixture, model_lease, proposed

from poolhouse.board.client import Denied
from poolhouse.board.session import Session
from poolhouse.memory import vault
from poolhouse.reputation.sealed import SealedGraph
from poolhouse.reputation.work import WorkLedger
from poolhouse.workspace import task_credit, work_reputation

pytest_plugins = ["node_kit"]
board = _board_fixture


@pytest.fixture
def ledger(tmp_path):
    held = WorkLedger(SealedGraph(tmp_path / 'credit.enc',
                      keys=vault.PassphraseKeys(lambda _: 'isolated credit key')))
    yield held
    held.sealed.close()


def session(board, who) -> Session:
    return board.node.session(who)


def reviewed(board, decision=None):
    proposal = proposed(board)
    result = board.tasks(board.lead).review(board.task['id'], decision or accepted())
    return proposal, result


def standing(board, who, ledger):
    return work_reputation.standings_on(session(board, who), ledger=ledger)


def test_review_earns_once_from_independent_checks_not_worker_claims(board, ledger):
    proposal, review = reviewed(board, {**accepted(),
        'quality': [{'kind': 'validated', 'reason': 'Reviewer reproduced native replay.',
                     'checks': ['Replay passes'], 'artifacts': ['replay.json']}],
        'review': {'quality': 90, 'reliability': 100, 'reason': 'Independent inspection passed.'},
        'verified_usage': {'source': 'Independent task review', 'tokens_in': None,
                           'tokens_out': 24, 'wall_seconds': 2.0}})
    lead = session(board, board.lead)
    first = task_credit.verify_on_board(lead, board.task['id'], ledger=ledger)
    again = task_credit.verify_on_board(lead, board.task['id'], ledger=ledger)
    assert first['credited'] and not again['credited'] and first['id'] == again['id']
    assert first['checks'] == review['checks'] and first['checks'] != proposal['checks']
    assert first['proposal_hash'] == proposal['proposal_hash']
    assert first['completion_hash'] == review['review_hash']
    row = standing(board, board.worker, ledger)['own']
    assert row['economy']['balance'] == 15 and row['economy']['spent'] == 0
    assert row['work_reputation']['reliability'] == 66.67
    assert row['work_reputation']['reliability_samples'] == 1
    assert row['work_reputation']['quality_samples'] == 1
    assert row['economy']['usage']['tokens_out'] == 24
    assert first['provenance']['model'] == 'qwen'
    graph = ledger.sealed.graph()
    assert len(graph.nodes('work_contribution')) == 1 and len(graph.nodes('work_award')) == 1
    assert len(graph.nodes('work_rating')) == 1 and len(graph.nodes('work_usage')) == 1


@pytest.mark.redteam
def test_worker_and_unrelated_verifier_cannot_award_completion(board, ledger):
    reviewed(board)
    for who in (board.worker, board.other):
        with pytest.raises(Denied):
            task_credit.verify_on_board(session(board, who), board.task['id'], ledger=ledger)
    with pytest.raises(Denied):
        task_credit.verify_on_board(Session(board.node.client, board.node.board, ''), board.task['id'], ledger=ledger)
    assert ledger.standings(board.node.board) == []


@pytest.mark.parametrize('outcome, expected', [('rejected', 33.33), ('blocked_infrastructure', 50)])
def test_rejected_and_infrastructure_reviews_award_nothing_and_rate_fairly(board, ledger, outcome, expected):
    reviewed(board, {'accepted': False, 'outcome': outcome, 'reason': 'Independent rejected or blocked outcome.',
                     'checks': [{'name': 'Replay passes', 'passed': False}]})
    result = task_credit.verify_on_board(session(board, board.lead), board.task['id'], ledger=ledger)
    assert not result['credited']
    row = standing(board, board.worker, ledger)['own']
    assert row['economy']['balance'] == 0 and row['work_reputation']['reliability'] == expected
    assert row['work_reputation']['outcomes'][outcome] == 1
    assert row['work_reputation']['quality_samples'] == 0
    assert row['work_reputation']['reliability_samples'] == (1 if outcome == 'rejected' else 0)


def test_historical_ledger_namespace_migration_preserves_award_identity(ledger):
    old = hashlib.sha256(b'/old/workspace/path').hexdigest()
    current = 'workspace:' + '1' * 32
    data = {'workspace': old, 'agent': 'scout', 'task': 171, 'completion': 172,
            'verifier': 'lead', 'verified_at': 1000.0, 'task_hash': 'a' * 64,
            'completion_hash': 'b' * 64, 'checks': [{'name': 'Independent checks', 'passed': True}],
            'artifacts': {'result.json': 'c' * 64},
            'award': {'policy': 'completion-quality-v1', 'currency': 'work-credit',
                      'base': 10, 'quality_bonus': 0, 'total': 10, 'quality': []}}
    first = ledger.record(data)
    assert ledger.migrate_namespace(old, current) == 1
    assert ledger.migrate_namespace(old, current) == 0
    retry = ledger.record({**data, 'workspace': current})
    assert not retry['credited'] and retry['id'] == first['id']
    assert ledger.standings(current)[0]['economy']['balance'] == 10


def test_concurrent_award_and_outcome_record_once(board, ledger):
    from concurrent.futures import ThreadPoolExecutor

    reviewed(board)
    def award(_):
        sealed = SealedGraph(ledger.sealed.path, keys=ledger.sealed.keys)
        try:
            return task_credit.verify_on_board(session(board, board.lead), board.task['id'],
                                           ledger=WorkLedger(sealed))['credited']
        finally:
            sealed.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(award, range(2)))
    assert sorted(results) == [False, True]
    row = standing(board, board.worker, ledger)['own']
    assert row['economy']['balance'] == 10 and row['work_reputation']['outcomes']['accepted'] == 1


@pytest.mark.parametrize('model, balance', [('Qwen3.8-27B', 20), ('gemma', 10)])
def test_awards_follow_verified_model_family_not_device_or_worker_label(board, ledger, model, balance):
    reviewed(board)
    task_credit.verify_on_board(session(board, board.lead), board.task['id'], ledger=ledger)
    second = board.node.member('second-model', parent=board.lead)
    lease = model_lease(board.node, second, model)
    task = board.tasks(board.lead).create({**board.spec, 'source_key': 'repo:demo/sim:issue:43'})
    allocation = board.tasks(board.lead).assign(task['id'], second.name, lease, ['coding'])
    work = board.tasks(second)
    work.claim(task['id'], allocation['allocation_id'])
    work.submit(task['id'], {'artifacts': {'next.json': 'b' * 64},
                             'checks': [{'name': 'Worker claims tests', 'passed': True}],
                             'provenance': {'model': 'forged-qwen-label', 'runtime': 'Codex'}})
    board.tasks(board.lead).review(task['id'], accepted())
    task_credit.verify_on_board(session(board, board.lead), task['id'], ledger=ledger)
    row = standing(board, second, ledger)['own']
    members = balance // 10
    assert row['economy']['balance'] == balance and row['verified_tasks'] == members
    assert len(row['members']) == members and second.name in row['members']
    assert model in {item['provenance']['model'] for item in row['evidence']}
    assert row['account_type'] == 'model_family'
    team = standing(board, board.lead, ledger)['team']
    qwen = next(item for item in team if item['family_id'] == 'qwen')
    assert qwen['economy']['balance'] == (20 if members == 2 else 10)
    assert sum(item['economy']['balance'] for item in team) == 20
    assert len(ledger.sealed.graph().nodes('work_award')) == 2


@pytest.mark.redteam
def test_designated_member_review_awards_the_reviewer_and_only_the_reviewer_records_it(board, ledger):
    member = board.node.member('review-member')
    board.tasks(board.lead).set_mode('strict')
    task = board.tasks(board.lead).create({**board.spec, 'reviewers': [member.name], 'assignees': [board.worker.name],
                                           'source_key': 'independent-member'})
    allocation = board.tasks(board.lead).assign(task['id'], board.worker.name, board.lease, ['coding'])
    work = board.tasks(board.worker)
    work.claim(task['id'], allocation['allocation_id'])
    work.submit(task['id'], {'artifacts': {'reviewed.json': 'd' * 64},
                             'checks': [{'name': 'Worker claims acceptance', 'passed': True}]})
    board.tasks(member).review(task['id'], accepted())
    result = task_credit.verify_on_board(session(board, member), task['id'], ledger=ledger)
    assert result['credited'] and result['verifier'] == member.name
    with pytest.raises(Denied):
        task_credit.verify_on_board(session(board, board.lead), task['id'], ledger=ledger)
    assert standing(board, board.worker, ledger)['own']['economy']['balance'] == 10


def test_model_switch_binds_live_family_without_moving_historical_awards(board, ledger):
    board.tasks(board.worker).claim(board.task['id'], board.allocation['allocation_id'])
    pending = standing(board, board.worker, ledger)['own']
    assert pending['family_id'] == 'qwen' and pending['economy']['balance'] == 0
    board.tasks(board.worker).submit(board.task['id'], {'artifacts': {'replay.json': 'a' * 64},
                                                        'checks': [{'name': 'Worker claim', 'passed': True}]})
    board.tasks(board.lead).review(board.task['id'], accepted())
    award = task_credit.verify_on_board(session(board, board.lead), board.task['id'], ledger=ledger)
    lease = model_lease(board.node, board.worker, 'gemma')
    task = board.tasks(board.lead).create({**board.spec, 'source_key': 'model-switch'})
    allocation = board.tasks(board.lead).assign(task['id'], board.worker.name, lease, ['coding'])
    board.tasks(board.worker).claim(task['id'], allocation['allocation_id'])
    result = standing(board, board.worker, ledger)
    assert result['own']['family_id'] == 'gemma' and result['own']['economy']['balance'] == 0
    qwen = next(row for row in result['team'] if row['family_id'] == 'qwen')
    assert qwen['economy']['balance'] == 10 and qwen['evidence'][0]['id'] == award['id']
    assert qwen['evidence'][0]['provenance']['model'] == 'qwen'
    assert len(ledger.sealed.graph().nodes('work_award')) == 1


@pytest.mark.redteam
def test_accepted_native_task_earns_nothing_before_integration_completes(board, ledger):
    board.tasks(board.lead).scope(board.task['id'], {'state': 'reserved'})
    proposed(board)
    board.tasks(board.lead).review(board.task['id'], accepted())
    with pytest.raises(Denied, match='completed task'):
        task_credit.verify_on_board(session(board, board.lead), board.task['id'], ledger=ledger)
    assert ledger.standings(board.node.board) == []


def test_the_reputation_tool_reads_the_boards_own_standings(board, ledger, monkeypatch):
    from poolhouse.board.session import AGENT_ENV
    from poolhouse.workspace import tools

    reviewed(board)
    task_credit.verify_on_board(session(board, board.lead), board.task['id'], ledger=ledger)
    monkeypatch.setattr(work_reputation, 'WorkLedger', lambda: ledger)
    monkeypatch.setenv(AGENT_ENV, board.worker.name)
    assert tools.workspace_reputation()['own']['verified_tasks'] == 1


@pytest.mark.redteam
@pytest.mark.parametrize('change', [
    lambda t: t['review'].update(worker='someone-else'),
    lambda t: t['review'].update(proposal_hash='c' * 64),
    lambda t: t['review'].update(proposal_id='another-proposal'),
    lambda t: t['review'].update(task='another-task'),
    lambda t: t['review'].update(accepted=False),
    lambda t: t['review'].update(checks=[{'name': 'Replay passes', 'passed': False}]),
])
def test_an_award_binds_the_exact_proposal_and_outcome_it_was_reviewed_for(board, ledger, change):
    from poolhouse.workspace.identity import Denied as Refused

    reviewed(board)
    task = board.tasks(board.lead).get(board.task['id'])
    change(task)
    with pytest.raises(Refused):
        task_credit.award(task, board.lead.name, board.node.board, ledger=ledger)
    assert ledger.standings(board.node.board) == []

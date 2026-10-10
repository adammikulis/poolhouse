"""Actor and exact execution views share independently reviewed outcomes."""

import pytest
from task_kit import accepted, board as board_fixture, model_lease
from test_board_task_credit import ledger as ledger_fixture, reviewed, seat, standing

from poolhouse.workspace import task_credit
from poolhouse.workspace.task_provenance import snapshot

pytest_plugins = ["node_kit"]
board = board_fixture
ledger = ledger_fixture


def test_same_actor_switches_model_without_rewriting_receipts_or_duplicating_credit(board, ledger):
    first_proposal, _ = reviewed(board, {**accepted(), 'verified_usage': {
        'source': 'Independent task review', 'tokens_in': None, 'tokens_out': 24, 'wall_seconds': 2.0}})
    lead = seat(board, board.lead)
    first = task_credit.verify_on_board(lead, board.task['id'], ledger=ledger)
    lease = model_lease(board.node, board.worker, 'gemma')
    task = board.tasks(board.lead).create({**board.spec, 'source_key': 'second-model'})
    allocation = board.tasks(board.lead).assign(task['id'], board.worker.name, lease, ['coding'])
    work = board.tasks(board.worker)
    work.claim(task['id'], allocation['allocation_id'])
    proposed = work.submit(task['id'], {
        'artifacts': {'second.json': 'c' * 64}, 'checks': [{'name': 'Worker claims tests', 'passed': True}],
        'provenance': {'model': 'forged-model', 'runtime': 'forged-runtime'}})
    board.tasks(board.lead).review(task['id'], accepted())
    second = task_credit.verify_on_board(lead, task['id'], ledger=ledger)
    again = task_credit.verify_on_board(lead, task['id'], ledger=ledger)
    result = standing(board, board.worker, ledger)
    actor = next(row for row in result['dimensions']['agents'] if row['id'] == board.worker.name)
    assert actor['aliases'] == [board.worker.name] and actor['economy']['balance'] == 20
    assert actor['verified_tasks'] == 2 and actor['work_reputation']['reliability_samples'] == 2
    assert actor['economy']['usage']['tokens_out'] == 24
    models = result['dimensions']['models']
    assert len(models) == 2 and sum(row['economy']['balance'] for row in models) == 20
    assert sum(row['economy']['usage']['tokens_out'] or 0 for row in models) == 24
    assert sum(row['economy']['usage']['tokens_out'] or 0 for row in result['team']) == 24
    assert {row['evidence'][0]['provenance']['model'] for row in models} == {'qwen', 'gemma'}
    assert first['provenance'] == first_proposal['provenance']
    assert second['provenance'] == proposed['provenance']
    assert second['provenance']['runtime'] == 'claude-code' and second['provenance']['alias'] == board.worker.name
    assert second['provenance']['model_source'] == 'verified-serving-resource'
    assert proposed['claimed_provenance']['model'] == 'forged-model'
    assert not again['credited'] and again['id'] == second['id']
    assert sum(row['economy']['balance'] for row in result['team']) == 20
    assert len(ledger.sealed.graph().nodes('work_award')) == 2


@pytest.mark.parametrize('outcome,samples', [('rejected', 1), ('blocked_infrastructure', 0)])
def test_unaccepted_dimensions_keep_existing_review_policy_without_awards(board, ledger, outcome, samples):
    reviewed(board, {'accepted': False, 'outcome': outcome, 'reason': 'Independent outcome.',
                     'checks': [{'name': 'Replay passes', 'passed': False}]})
    task_credit.verify_on_board(seat(board, board.lead), board.task['id'], ledger=ledger)
    result = standing(board, board.worker, ledger)['dimensions']
    for kind in ('agents', 'models'):
        assert len(result[kind]) == 1
        row = result[kind][0]
        assert row['economy']['balance'] == 0 and row['verified_tasks'] == 0
        assert row['work_reputation']['reliability_samples'] == samples
        assert row['work_reputation']['outcomes'][outcome] == 1
    assert not ledger.sealed.graph().nodes('work_award')


def test_unknown_artifact_and_runtime_are_not_promoted_from_a_local_filename():
    resource = {'model': '/device/models/same.gguf', 'allocation_id': 'allocation:known'}
    result = snapshot('authenticated-worker', resource)
    assert result['model'] == resource['model'] and result['actor'] == 'authenticated-worker'
    assert result['artifact']['source'] == 'unknown' and result['artifact']['id'] is None
    assert result['broker_runtime']['source'] == 'unknown'
    assert result['alias_source'] == 'unknown' and result['alias'] is None


@pytest.mark.slow
def test_history_filters_actor_exact_runtime_and_family_over_the_same_review(board, ledger, tmp_path, monkeypatch, playwright):
    from playwright.sync_api import expect
    from test_fleet_ui import Serving

    from poolhouse.workspace import tokens, work_reputation
    from poolhouse.workspace.service import Workspace

    reviewed(board)
    task_credit.verify_on_board(seat(board, board.lead), board.task['id'], ledger=ledger)
    ws = Workspace(tmp_path / 'ws')
    tokens.store(ws.base, tokens.OWNER_FILE, ws.init('owner'))
    monkeypatch.setattr(work_reputation, 'Workspace', lambda: ws)
    monkeypatch.setattr(work_reputation, 'WorkLedger', lambda: ledger)
    monkeypatch.setattr(work_reputation, 'scope', lambda _: board.node.board)
    server = Serving(tmp_path)
    server.ui.settings.setup_done = True
    try:
        with playwright.chromium.launch(headless=True) as browser:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.goto(f'http://127.0.0.1:{server.port}/ui/#history')
            view = page.locator('history-view')
            view.locator('details.activity-reputation > summary').click()
            credits = view.locator('#history-credits')
            expect(credits.locator('summary').filter(has_text='Qwen · 10 credits')).to_be_visible()
            view.locator('#history-dimension').select_option('agent')
            card = credits.locator('details.history-action').filter(has_text=f'{board.worker.name} · 10 credits')
            card.locator(':scope > summary').click()
            expect(card.get_by_text('These are views of the same contributions; credits are awarded once.', exact=True)).to_be_visible()
            expect(card.get_by_text(f'Account identity: {board.worker.name}', exact=True)).to_be_visible()
            view.locator('#history-dimension').select_option('model')
            card = credits.locator('details.history-action').filter(has_text='qwen · claude-code · 10 credits')
            card.locator(':scope > summary').click()
            expect(card.get_by_text('Model source: verified-serving-resource · Runtime source: registered-session-harness · Artifact: unknown', exact=True)).to_be_visible()
            card.locator('summary').filter(has_text=f'Task {board.task["id"]} · verified by {board.lead.name}').click()
            expect(card.get_by_text('replay.json', exact=False)).to_be_visible()
            assert view.locator('section.workspace').evaluate(
                '(element) => element.scrollWidth <= element.clientWidth && element.clientWidth <= innerWidth')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:
        server.close()

"""Person review persists independently of encrypted credit availability and retries once."""

import pytest
from person_kit import person_seat
from task_kit import accepted, board, proposed

from poolhouse.board.tasks import Tasks
from poolhouse.memory import vault
from poolhouse.reputation.sealed import SealedGraph
from poolhouse.reputation.work import WorkLedger
from poolhouse.workspace import task_credit, task_routes, work_reputation

__all__ = ['board']
pytest_plugins = ["node_kit", "person_kit"]
pytestmark = pytest.mark.redteam


def test_person_review_records_credit_after_locked_store_retry_without_losing_review(board, tasks_ui, monkeypatch, tmp_path):
    proposed(board)
    server, node = tasks_ui
    ledger = WorkLedger(SealedGraph(tmp_path / 'credit.enc', keys=vault.PassphraseKeys(lambda _: 'isolated task key')))
    available = [False]
    original = task_credit.verify_on_board
    def verify(seat, ident):
        if not available[0]:
            raise vault.KeyUnavailable('fake locked store')
        return original(seat, ident, ledger=ledger)
    monkeypatch.setattr(task_routes.task_outcomes.task_credit, 'verify_on_board', verify)
    def post(body):
        return server.call('/ui/tasks', method='POST', body=body,
                           headers={'Origin': f'http://127.0.0.1:{server.port}'})
    try:
        code, result, _ = post({'action': 'review', 'id': board.task['id'], 'decision': accepted()})
        assert code == 200 and result['credit']['state'] == 'pending'
        saved = Tasks(person_seat(node)).get(board.task['id'])
        assert saved['state'] == 'completed' and saved['review']['accepted']
        available[0] = True
        code, result, _ = post({'action': 'credit', 'id': board.task['id']})
        assert code == 200 and result['credit']['credited'] and result['credit']['total'] == 10
        assert post({'action': 'credit', 'id': board.task['id']})[1]['credit']['credited'] is False
        standing = work_reputation.standings_on(person_seat(node), ledger=ledger)
        assert sum(row['economy']['balance'] for row in standing['team']) == 10
        assert post({'action': 'credit', 'id': board.task['id'], 'award': 999})[0] == 400
    finally:
        ledger.sealed.close()

"""Optional person review quality links actual checks and immutable artifacts to bounded awards."""

from types import SimpleNamespace

import pytest
from browser_expect import expect
from person_kit import PROJECT, person_session
from task_kit import board, proposed

from poolhouse.board.tasks import Tasks
from poolhouse.memory import vault
from poolhouse.reputation.sealed import SealedGraph
from poolhouse.reputation.work import WorkLedger
from poolhouse.workspace import task_credit, task_outcomes, work_reputation

__all__ = ['board']
pytest_plugins = ["node_kit", "person_kit"]
pytestmark = [pytest.mark.slow, pytest.mark.redteam]


def registry(root):
    """The project registry the page lists: one project, whose checkout is ``root``."""
    row = {'id': PROJECT, 'name': 'experiment', 'machine': 'local', 'board_host': ''}
    project = SimpleNamespace(name='experiment', root=str(root), board_host='')
    return SimpleNamespace(machine='local', boards=lambda: [row], list=lambda: [], candidates=lambda: [],
                           hosts=lambda host: True, get=lambda ident: project)


def fill_review(viewer):
    """The independent review form with its advanced quality section filled in."""
    viewer.get_by_label('Independent review reason').fill('Independent replay meets acceptance')
    viewer.get_by_text('Review quality — advanced', exact=True).click()
    viewer.get_by_label('Validated result', exact=True).check()
    viewer.get_by_label('Validated result reason').fill('Replay independently reproduced')
    viewer.get_by_label('Validated result checks').select_option('Replay passes')
    viewer.get_by_label('Validated result artifacts').select_option('replay.json')
    viewer.get_by_label('Add independent ratings', exact=True).check()
    viewer.get_by_label('Quality rating', exact=True).fill('85')
    viewer.get_by_label('Reliability assessment', exact=True).fill('80')
    viewer.get_by_label('Rating reason', exact=True).fill('Reproduced evidence and clear implementation')


def test_advanced_person_quality_review_records_fixed_bonus_and_separate_rating(board, tasks_ui, monkeypatch, tmp_path, playwright):
    proposed(board)
    server, node = tasks_ui
    server.ui.projects = registry(tmp_path)
    server.ui.settings.setup_done = True
    server.ui.workspaces = object()
    ledger = WorkLedger(SealedGraph(tmp_path / 'quality.enc', keys=vault.PassphraseKeys(lambda _: 'isolated quality key')))
    verify = task_credit.verify_on_board
    monkeypatch.setattr(task_outcomes.task_credit, 'verify_on_board', lambda session, ident: verify(session, ident, ledger=ledger))
    try:
        with playwright.chromium.launch(headless=True) as browser:
            context = browser.new_context(viewport={'width': 390, 'height': 844})
            name, _, value = server.cookie.partition('=')
            context.add_cookies([{'name': name, 'value': value, 'url': f'http://127.0.0.1:{server.port}'}])
            page = context.new_page()
            page.goto(f'http://127.0.0.1:{server.port}/ui/#tasks')
            viewer = page.locator('tasks-view')
            viewer.get_by_role('button', name='Inspect native simulation').click()
            viewer.get_by_text('Independently review this proposal', exact=True).click()
            advanced = viewer.get_by_text('Review quality — advanced', exact=True).locator('..')
            assert advanced.get_attribute('open') is None
            fill_review(viewer)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path='/private/tmp/poolhouse-task-quality-form.png', full_page=True)
            viewer.get_by_role('button', name='Record independent review', exact=True).click()
            expect(viewer.locator('.status')).to_contain_text('checks you independently verified')
            assert Tasks(person_session(node)).get(board.task['id'])['review'] is None
            viewer.get_by_label('Replay passes', exact=True).check()
            viewer.get_by_role('button', name='Record independent review', exact=True).click()
            expect(viewer.locator('.badge')).to_have_text('completed')
            expect(viewer.locator('.status')).to_contain_text('15 credits')
            page.screenshot(path='/private/tmp/poolhouse-task-quality-preview.png', full_page=True)
        task = Tasks(person_session(node)).get(board.task['id'])
        assert task['review']['quality'] == [{'kind': 'validated', 'reason': 'Replay independently reproduced',
                                             'checks': ['Replay passes'], 'artifacts': ['replay.json']}]
        assert task['review']['review']['quality'] == 85
        standing = work_reputation.standings_on(person_session(node), ledger=ledger)
        earned = next(row for row in standing['team'] if row['economy']['earned'])
        assert earned['economy']['completion_credits'] == 10
        assert earned['economy']['quality_credits'] == 5
        assert earned['economy']['balance'] == 15
        assert earned['work_reputation']['quality_samples'] == 1
    finally:
        ledger.sealed.close()

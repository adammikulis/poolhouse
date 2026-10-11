"""Authenticated completion credit, immutable evidence, encrypted reuse and source-risk separation, on a real node."""

import concurrent.futures
import time

import pytest
from local_kit import team  # noqa: F401

from poolhouse import node_launch
from poolhouse.board.session import Session
from poolhouse.errors import Denied
from poolhouse.memory import vault
from poolhouse.reputation import economy
from poolhouse.reputation.sealed import SealedGraph
from poolhouse.reputation.store import Ledger
from poolhouse.reputation.work import WorkLedger
from poolhouse.workspace import work_reputation
from poolhouse.workspace.work_reputation import Roster, standings_of, standings_on

pytest_plugins = ["node_kit"]


@pytest.fixture
def work(team, tmp_path):  # noqa: F811
    kit = team
    kit.parent = kit.session(kit.lead)
    kit.scout = kit.node.member('scout', parent=kit.lead)
    kit.agent_id = kit.scout.name
    kit.child = kit.session(kit.scout)
    kit.task = {'seq': 171}
    kit.done = {'seq': 172}
    kit.evidence = {'task': kit.task['seq'], 'completion': kit.done['seq'],
                    'checks': [{'name': 'native regression suite', 'passed': True}],
                    'artifacts': {'result.json': 'a' * 64}}
    keys = vault.PassphraseKeys(lambda _: 'test work reputation passphrase')
    sealed = SealedGraph(tmp_path / 'reputation.enc', keys=keys)
    kit.ledger = WorkLedger(sealed)
    yield kit
    sealed.close()


def _historical(work, evidence=None, *, ledger=None):
    """Seed already-verified historical graph evidence without inventing task state."""
    raw = dict(work.evidence if evidence is None else evidence)
    return (ledger or work.ledger).record({**raw, 'award': economy.assessment(raw),
                                         'agent': raw.get('agent', work.agent_id), 'verifier': 'lead',
                                         'workspace': work.node.board, 'verified_at': time.time(),
                                         'task_hash': 'b' * 64, 'completion_hash': 'c' * 64})


def standing(work, who=None, **kw):
    """The standings the session ``who`` (default the scout) reads from the work ledger."""
    return standings_on(who or work.child, ledger=kw.pop('ledger', work.ledger), **kw)


def test_verified_task_credits_once_and_reopens_with_evidence(work):
    first = _historical(work)
    repeated = _historical(work)
    assert first['credited'] and not repeated['credited']
    assert first['task_hash'] == 'b' * 64
    assert first['completion_hash'] == 'c' * 64
    assert standing(work)['own']['verified_tasks'] == 1
    sealed = work.ledger.sealed
    assert work.agent_id.encode() not in sealed.path.read_bytes() and b'result.json' not in sealed.path.read_bytes()
    sealed.close()
    reopened = WorkLedger(SealedGraph(sealed.path, keys=sealed.keys))
    try:
        result = standing(work, ledger=reopened)
        assert result['own']['verified_tasks'] == 1 and result['own']['evidence'][0]['artifacts'] == {'result.json': 'a' * 64}
    finally:
        reopened.sealed.close()


def test_historical_credit_and_source_risk_are_separate(work):
    source = Ledger(work.ledger.sealed.path, keys=work.ledger.sealed.keys, flush_s=0)
    try:
        before = source.observe('host', 'bad.example', 'scan_hit')
        _historical(work)
        after = source.standing('host', 'bad.example')
        assert before.state == after.state == 'bad' and before.clean == after.clean == 0
        assert work.ledger.standings(work.node.board)[0]['verified_tasks'] == 1
    finally:
        source.close()


def test_concurrent_historical_record_import_is_idempotent(work):
    def credit(_):
        sealed = SealedGraph(work.ledger.sealed.path, keys=work.ledger.sealed.keys)
        try:
            return _historical(work, ledger=WorkLedger(sealed))['credited']
        finally:
            sealed.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(credit, range(2)))
    assert sorted(results) == [False, True]
    assert work.ledger.standings(work.node.board)[0]['verified_tasks'] == 1


@pytest.mark.redteam
def test_person_reputation_route_is_read_only_and_session_guarded(work, tmp_path, monkeypatch):
    from launch_support import signed
    from test_fleet_ui import Serving

    from poolhouse.fleet import routes

    monkeypatch.setattr(node_launch, 'default_state', lambda: work.node.state)
    monkeypatch.setattr(work_reputation, 'WorkLedger', lambda: work.ledger)
    server = signed(Serving(tmp_path))
    try:
        assert server.call('/ui/work-reputation/standings', ui_header=False)[0] == 403
        monkeypatch.setattr(routes, 'in_pool', lambda _: True)
        assert server.call('/ui/work-reputation/standings', cookie='')[0] == 401
        cookie = server.ui.sessions.cookie_header(server.ui.sessions.open('person', 'launch-ticket'))
        assert server.call('/ui/work-reputation/standings', method='POST', body={}, cookie=cookie)[0] == 405
        assert server.call('/ui/work-reputation/standings?offset=-1', cookie=cookie)[0] == 400
        assert server.call('/ui/work-reputation/standings?agent=../../owner', cookie=cookie)[0] == 400
        _historical(work)
        code, result, _ = server.call('/ui/work-reputation/standings', cookie=cookie)
        assert code == 200
        assert next(item for item in result['team'] if item['agent'] == work.agent_id)['verified_tasks'] == 1
        assert work.node.members['scout'].token not in str(result) and work.lead.token not in str(result)
    finally:
        server.close()


@pytest.mark.slow
def test_history_shows_verified_score_and_expandable_parent_evidence(work, tmp_path, monkeypatch, playwright):
    from playwright.sync_api import expect
    from test_fleet_ui import Serving

    from poolhouse.activity import writer
    from poolhouse.activity.log import ActivityLog
    from poolhouse.activity.schema import build
    from poolhouse.workspace import localagent

    monkeypatch.setattr(node_launch, 'default_state', lambda: work.node.state)
    monkeypatch.setattr(work_reputation, 'WorkLedger', lambda: work.ledger)
    log = ActivityLog(tmp_path / 'activity', key=lambda: bytes(range(32)))
    localagent.save(work.base, localagent.Agent('local-qwen', 'qwen.gguf', identity=work.agent_id, board=work.node.board))
    log.add(build('agent.task', ts=time.time(), actor='local-qwen', session='native-session',
                  subject='Simulator check', outcome='completed'))
    monkeypatch.setattr(writer, 'log', lambda: log)
    _historical(work)
    server = Serving(tmp_path)
    server.ui.settings.setup_done = True
    try:
        with playwright.chromium.launch(headless=True) as browser:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.goto(f'http://127.0.0.1:{server.port}/ui/#history')
            viewer = page.locator('history-view')
            viewer.locator('details.activity-reputation > summary').click()
            viewer.locator('#history-credits > details > summary').filter(has_text=work.agent_id).click()
            viewer.get_by_text(f"Task {work.task['seq']} · verified by lead", exact=True).click()
            expect(viewer.get_by_text('native regression suite', exact=False)).to_be_visible()
            expect(viewer.get_by_text('result.json', exact=False)).to_be_visible()
            expect(viewer.get_by_text(f'Account identity: {work.agent_id}', exact=True)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path='/private/tmp/poolhouse-work-reputation-history.png', full_page=True)
    finally:
        server.close()


@pytest.mark.redteam
def test_reputation_read_requires_a_token_the_node_knows(work):
    for token in ('', 'f' * 64):
        with pytest.raises(Denied):
            standings_on(Session(work.node.client, work.node.board, token, work.agent_id), ledger=work.ledger)
    result = standing(work)
    assert result['own']['verified_tasks'] == 0
    assert any(item['agent'] == work.lead.name and item['verified_tasks'] == 0 for item in result['team'])


@pytest.mark.redteam
def test_local_tools_read_reputation_and_task_frame_contains_team_awareness(work, monkeypatch):
    from poolhouse.workspace import localloop, localtools

    _historical(work)
    monkeypatch.setattr(work_reputation, 'WorkLedger', lambda: work.ledger)
    state = localtools.TaskState()
    extension = localtools.workspace_extension(work.child, state, lambda _: True)
    functions = {schema['function']['name']: call for schema, call in extension.tools()}
    result = functions['workspace_reputation']()
    assert result['own']['verified_tasks'] == 1 and 'workspace_reputation' in extension.reads
    assert not any('verify' in name or 'award' in name for name in functions)
    frame = localloop._frame({'seq': work.task['seq'], 'from': 'lead', 'text': 'Inspect the simulator.'},
                            'parent', work_reputation.brief_on(work.child))
    assert 'Your verified completions: 1' in frame and f'{work.agent_id}: 1 verified tasks' in frame
    assert 'no authority' in frame


def test_evidence_pages_keep_total_score_and_expose_earlier_records(work):
    first = _historical(work)
    for task in range(100, 121):
        work.ledger.record({**{key: value for key, value in first.items() if key not in ('id', 'credited')},
                            'task': task, 'verified_at': float(task)})
    first_page = standing(work, agent=work.agent_id)['team'][0]
    next_page = standing(work, agent=work.agent_id, offset=20)['team'][0]
    assert first_page['verified_tasks'] == next_page['verified_tasks'] == 22
    assert len(first_page['evidence']) == 20 and first_page['evidence_held'] == 2
    assert len(next_page['evidence']) == 2 and next_page['evidence_held'] == 0
    assert not {item['id'] for item in first_page['evidence']} & {item['id'] for item in next_page['evidence']}


def test_free_economy_separates_credits_ratings_and_unknown_usage(work):
    first = _historical(work)
    row = standing(work)['own']
    assert row['verified_tasks'] == 1 and row['economy']['balance'] == 10
    assert row['economy']['spent'] == 0 and row['economy']['mode'] == 'free'
    assert row['economy']['usage']['recorded'] is False
    assert row['economy']['usage']['tokens_in'] is None
    assert row['work_reputation']['state'] == 'unrated'
    assert row['pricing']['price'] == 0 and not row['pricing']['active_charging']
    assert first['award'] == {'policy': 'completion-quality-v1', 'currency': 'work-credit',
                              'base': 10, 'quality_bonus': 0, 'total': 10, 'quality': []}


def reviewed_evidence(work):
    return {**work.evidence,
            'quality': [{'kind': 'regression', 'reason': 'Reviewer reproduced the fixed failure.',
                         'checks': ['native regression suite'], 'artifacts': ['result.json']}],
            'review': {'quality': 90, 'reliability': 80, 'reason': 'Independent review passed.'},
            'usage': {'source': 'native harness measurement', 'tokens_in': None,
                      'tokens_out': 42, 'wall_seconds': 2.5},
            'provenance': {'model': 'first.gguf', 'harness': 'codex'}}


def test_quality_bonus_requires_reviewed_evidence_and_does_not_multiply_by_test_count(work):
    evidence = reviewed_evidence(work)
    first = _historical(work, evidence)
    repeated = _historical(work, {**evidence, 'quality': []})
    assert first['credited'] and not repeated['credited']
    row = standing(work)['own']
    assert row['economy']['balance'] == 15 and row['economy']['quality_credits'] == 5
    assert row['work_reputation']['quality'] == 63.33
    assert row['work_reputation']['reliability'] == 60
    assert row['work_reputation']['samples'] == 1
    assert row['economy']['usage']['tokens_in'] is None
    assert row['economy']['usage']['tokens_out'] == 42
    assert row['economy']['usage']['wall_seconds'] == 2.5
    assert row['pricing']['baseline_guaranteed'] and row['pricing']['price'] == 0


@pytest.mark.redteam
@pytest.mark.parametrize('change', [
    {'quality': [{'kind': 'regression', 'reason': 'Claimed', 'checks': ['invented'], 'artifacts': ['result.json']}]},
    {'quality': [{'kind': 'impact', 'reason': 'Claimed', 'checks': ['native regression suite'], 'artifacts': ['missing']}]},
    {'review': {'quality': True, 'reliability': 80, 'reason': 'Claimed'}},
    {'review': {'quality': 101, 'reliability': 80, 'reason': 'Claimed'}},
    {'usage': {'source': 'native', 'tokens_in': -1, 'tokens_out': 1, 'wall_seconds': 1}},
    {'usage': {'source': 'native', 'tokens_in': 1, 'tokens_out': 1, 'wall_seconds': float('nan')}},
])
def test_unverified_or_invalid_quality_and_resource_claims_never_credit(work, change):
    with pytest.raises(ValueError):
        _historical(work, {**work.evidence, **change})
    assert standing(work)['own']['economy']['balance'] == 0


def test_model_switches_keep_one_authenticated_agent_account(work):
    _historical(work, reviewed_evidence(work))
    task, done = {'seq': 173}, {'seq': 174}
    evidence = {**reviewed_evidence(work), 'task': task['seq'], 'completion': done['seq'],
                'provenance': {'model': 'second.gguf', 'harness': 'claude'}}
    _historical(work, evidence)
    result = standing(work)
    row = result['own']
    assert row['agent'] == work.agent_id and row['economy']['balance'] == 30
    assert row['verified_tasks'] == 2 and row['work_reputation']['samples'] == 2
    assert {item['provenance']['model'] for item in row['evidence']} == {'first.gguf', 'second.gguf'}
    assert len([item for item in result['team'] if item['agent'] == work.agent_id]) == 1


def test_historical_awards_without_verified_family_stay_with_their_workers(work):
    _historical(work, reviewed_evidence(work))
    another = work.node.member('second', parent=work.lead)
    task, done = {'seq': 173}, {'seq': 174}
    _historical(work, {**reviewed_evidence(work), 'agent': another.name, 'task': task['seq'],
                       'completion': done['seq'], 'provenance': {'model': 'second.gguf', 'harness': 'claude'}})
    second = work.session(another)
    roster = Roster(work.node.board, another.name, [a.name for a in second.agents()], {another.name: ['worker-two']}, {})
    row = standings_of(roster, ledger=work.ledger)['own']
    assert row['agent'] == another.name and row['agent'] != work.agent_id
    assert row['account_type'] == 'unassigned'
    assert row['economy']['balance'] == 15 and row['verified_tasks'] == 1
    assert row['members'] == [another.name]
    assert {item['agent'] for item in row['evidence']} == {another.name}
    assert 'worker-two' in row['aliases']
    first = standing(work)['own']
    assert first['agent'] == work.agent_id and first['economy']['balance'] == 15


def test_awards_ratings_and_usage_are_linked_graph_evidence_and_migration_is_once(work, monkeypatch):
    _historical(work, reviewed_evidence(work))
    graph = work.ledger.sealed.graph()
    assert len(graph.nodes('work_award')) == 1
    assert len(graph.nodes('work_quality_review')) == 1
    assert len(graph.nodes('work_rating')) == 1 and len(graph.nodes('work_usage')) == 1
    assert any(edge['rel'] == 'earned' for edge in graph.edges())
    assert any(edge['rel'] == 'supported_by' for edge in graph.edges())
    assert work.ledger.migrate_credit_awards() == 0
    node = graph.nodes('work_evidence')[0]
    historical = {**node['attrs']}
    historical.pop('award')
    def remove_award(current):
        current.upsert_node({**node, 'attrs': historical})
    work.ledger.sealed.edit(remove_award)
    assert standing(work)['own']['economy']['pending_awards'] == 1
    assert work.ledger.migrate_credit_awards() == 1
    assert work.ledger.migrate_credit_awards() == 0
    monkeypatch.setattr('poolhouse.reputation.economy.BASE_CREDITS', 1000)
    assert standing(work)['own']['economy']['balance'] == 10


def test_forgetting_source_risk_preserves_work_awards_and_discards_stale_backup(work):
    _historical(work)
    source = Ledger(work.ledger.sealed.path, keys=work.ledger.sealed.keys, flush_s=0)
    try:
        source.observe('host', 'bad.example', 'scan_hit')
        assert source.forget_all() == 1
        assert source.sources() == [] and not source.sealed.prev.exists()
        row = standing(work)['own']
        assert row['economy']['balance'] == 10 and row['verified_tasks'] == 1
        assert len(work.ledger.sealed.graph().nodes('work_award')) == 1
    finally:
        source.close()

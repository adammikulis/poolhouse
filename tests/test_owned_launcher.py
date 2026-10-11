"""Local launch callers start a worker as the session they act as, and nothing else."""

import json
from types import SimpleNamespace

import pytest
from local_kit import team  # noqa: F401
from node_kit import node_binary, workspace_node  # noqa: F401

from poolhouse.workspace import localagent as la, localcli, localroute, localstart


@pytest.mark.parametrize('caller', ['cli', 'browser'])
def test_launch_callers_start_the_worker_as_their_own_session(monkeypatch, tmp_path, caller):
    seen = {}
    starter = SimpleNamespace(name='launcher', board='demo')

    def start(session, ask, **options):
        seen.update(session=session, ask=ask, options=options)
        return localstart.Started('worker', 123, 'qwen', 'read-only', already=True, identity='qwen-abc123')
    monkeypatch.setattr(localstart, 'start', start)
    if caller == 'cli':
        monkeypatch.setattr(localcli.localmodel, 'choose', lambda *_args, **_kw:
                            SimpleNamespace(ok=True, name='qwen', note='fits'))
        args = SimpleNamespace(agent='', profile='coding', ctx='', project='.', role='read-only',
            model='qwen', name='worker', effort='off', max_effort='medium', orders_from='',
            harness='claude', repo='sample/project', no_wait=True, max_output_tokens=12345)
        assert localcli._start(args, starter, tmp_path) == 0
    else:
        code, _ = localroute._write(tmp_path, lambda: starter, 'start', json.dumps({
            'model': 'qwen', 'name': 'worker', 'profile': 'coding', 'project': '',
            'role': 'read-only', 'repo': 'sample/project', 'max_output_tokens': 12345}).encode())
        assert code == 200
    assert seen['session'] is starter and seen['options']['base'] == tmp_path
    assert seen['ask'].repo == 'sample/project' and seen['ask'].max_output_tokens == 12345


def test_a_worker_is_registered_as_its_starters_subagent_before_its_process_starts(team, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(localstart.lp, 'admit', lambda *_: ('', ''))
    monkeypatch.setattr(localstart.localmodel, 'context_for', lambda *_args, **_kwargs: 65536)
    events = []

    def spawn(*_args, **_kwargs):
        joined = {a.name: a for a in team.person.agents()}
        assert [name for name, a in joined.items() if a.parent == team.lead.name] == [la.load(team.base, 'worker').identity]
        events.append('started')
        return SimpleNamespace(pid=123, log=tmp_path / 'worker.log')
    monkeypatch.setattr(localstart, 'started_at', lambda *_: 42)
    pick = localstart.localmodel.Pick(ref='qwen', name='qwen')
    got = localstart.start(team.session(team.lead), localstart.Ask(name='worker', max_output_tokens=12345),
                           base=team.base, pick=pick, spawn=spawn)
    assert events == ['started']
    saved = la.load(team.base, 'worker')
    assert saved.ctx == 65536 and saved.max_output_tokens == 12345 and saved.identity == got.identity
    assert saved.board == team.node.board


def test_only_the_session_that_started_a_worker_or_the_person_manages_it(team, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(localstart.lp, 'admit', lambda *_: ('', ''))
    monkeypatch.setattr(localstart.localmodel, 'context_for', lambda *_args, **_kwargs: 65536)
    monkeypatch.setattr(localstart, 'started_at', lambda *_: 42)
    spawn = lambda *_a, **_k: SimpleNamespace(pid=123, log=tmp_path / 'worker.log')  # noqa: E731
    localstart.start(team.session(team.lead), localstart.Ask(name='worker'), base=team.base,
                     pick=localstart.localmodel.Pick(ref='qwen', name='qwen'), spawn=spawn)
    worker = la.load(team.base, 'worker')
    assert la.manages(team.session(team.lead), worker) and la.manages(team.person, worker)
    assert not la.manages(team.session(team.stranger), worker)


@pytest.mark.redteam
def test_agent_launch_claims_its_own_workers_model_and_leaves_the_launchers_alone(team, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(localstart.lp, 'admit', lambda *_: ('', ''))
    monkeypatch.setattr(localstart.localmodel, 'context_for', lambda *_args, **_kwargs: 65536)
    monkeypatch.setattr(localstart, 'started_at', lambda *_: 42)
    spawn = lambda *_a, **_k: SimpleNamespace(pid=123, log=tmp_path / 'worker.log')  # noqa: E731
    got = localstart.start(team.session(team.lead), localstart.Ask(name='worker'), base=team.base,
                           pick=localstart.localmodel.Pick(ref='qwen', name='qwen'), spawn=spawn)
    known = {a.name: a for a in team.person.agents()}
    child, parent = known[got.identity], known[team.lead.name]
    assert (child.model, child.model_state, child.harness) == ('qwen', 'claimed', 'poolhouse-agent')
    assert parent.model == 'claude-sonnet-5-5' and parent.harness == 'claude-code'

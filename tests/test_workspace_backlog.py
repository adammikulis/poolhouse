"""Authorized issue selection, durable leases, retries and native worker dispatch, on a real node."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from local_kit import MODEL, team  # noqa: F401

from poolhouse.board.tasks import Tasks
from poolhouse.errors import Denied
from poolhouse.net import git
from poolhouse.workspace import (
    backlog,
    issuepump,
    localagent as la,
    localcli,
)

pytest_plugins = ["node_kit"]


def worker_of(kit, parent, project, name="worker"):
    """A coding worker that ``parent`` started: its session on the board and its saved record."""
    member = kit.node.member(name, parent=parent)
    agent = la.Agent(name, "local.gguf", identity=member.name, board=kit.node.board, profile="coding", project=str(project))
    la.save(kit.base, agent)
    return agent


@pytest.fixture
def setup(team, tmp_path):  # noqa: F811
    kit = team
    parent = kit.session(kit.lead)
    project = tmp_path / "isolated"
    project.mkdir()
    git.run(["init", str(project)])
    (project / "source.py").write_text("VALUE = True\n")
    git.run(["add", "source.py"], cwd=project)
    git.run(["-c", "user.name=Test", "-c", "user.email=test@example.invalid",
             "commit", "-m", "baseline"], cwd=project)
    agent = worker_of(kit, kit.lead, project)
    backlog.configure(parent, kit.base, agent.name, "sample/project", str(project))
    return kit, parent, agent, project


def issue(number=1, **extra):
    return {"number": number, "title": "Add a missing feature", "body": "Acceptance: a real patch.",
            "url": f"https://github.com/sample/project/issues/{number}", "updatedAt": "revision1",
            "assignees": [], "labels": [], **extra}


def record_of(kit, key):
    return backlog.edited(kit.base, lambda ledger: dict(ledger["records"][key]))


def inbox(kit, agent):
    return kit.session(kit.node.adopt(agent.identity)).read_all(inbox=True)


@pytest.mark.redteam
def test_repository_infers_github_origin_as_argument_vector(monkeypatch, tmp_path):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout="https://github.com/sample/project.git\n")

    monkeypatch.setattr(backlog.subprocess, "run", run)
    assert backlog.repository(tmp_path) == "sample/project"
    argv, options = calls[0]
    assert argv == ["git", "remote", "get-url", "origin"]
    assert options["cwd"] == tmp_path and options["timeout"] == 5 and options["check"] is True
    assert "shell" not in options
    monkeypatch.setattr(backlog.subprocess, "run", lambda *_args, **_kwargs:
                        SimpleNamespace(stdout="https://github.com/sample/project;id"))
    assert backlog.repository(tmp_path) == ""


@pytest.mark.redteam
def test_repository_rejects_traversal_owner_in_origin(monkeypatch, tmp_path):
    monkeypatch.setattr(backlog.subprocess, "run", lambda *_args, **_kwargs:
                        SimpleNamespace(stdout="https://github.com/../repo.git"))
    assert backlog.repository(tmp_path) == ""


def test_worker_cannot_authorize_itself_or_another_parents_worker(setup):
    kit, _, agent, project = setup
    for who in (kit.node.adopt(agent.identity), kit.stranger):
        with pytest.raises(Denied):
            backlog.configure(kit.session(who), kit.base, agent.name, "sample/project", str(project))
    with pytest.raises(ValueError, match="owner/name"):
        backlog.configure(kit.person, kit.base, agent.name, "../outside", str(project))
    with pytest.raises(ValueError, match="registered git checkout"):
        backlog.configure(kit.person, kit.base, agent.name, "sample/project", str(project.parent))


def test_issue_subscription_is_easy_to_add_and_remove(setup):
    kit, _, _, _ = setup
    subscribed = backlog.subscribe_issue(kit.person, kit.base, 'sample/project#3')
    assert subscribed['subscribed'] and backlog.issue_watchers(kit.base, 'sample/project', 3) == [kit.person.name]
    removed = backlog.subscribe_issue(kit.person, kit.base, 'sample/project#3', False)
    assert not removed['subscribed'] and backlog.issue_watchers(kit.base, 'sample/project', 3) == []


def test_two_workers_cannot_claim_the_same_issue_and_reopen_preserves_result(setup):
    kit, parent, agent, project = setup
    other = worker_of(kit, kit.lead, project, "second")
    backlog.configure(parent, kit.base, other.name, "sample/project", str(project))
    with ThreadPoolExecutor(2) as pool:
        jobs = list(pool.map(lambda a: backlog.pick(kit.base, a, fetcher=lambda _: [issue()]), (agent, other)))
    chosen = next(row for row, _ in jobs if row)
    assert sum(row is not None for row, _ in jobs) == 1
    record = record_of(kit, chosen["key"])
    owner = agent if record["owner"] == agent.identity else other
    assert record["state"] == "working"
    backlog.finish(kit.base, chosen, owner, ("answer", "Reviewable patch proposed"))
    assert record_of(kit, chosen["key"])["state"] == "proposed"
    assert backlog.pick(kit.base, agent, fetcher=lambda _: [issue()])[0] is None
    assert backlog.pick(kit.base, other, fetcher=lambda _: [issue()])[0] is None


def test_blocked_issue_does_not_retry_after_time_or_issue_updates(setup):
    kit, _, agent, _ = setup
    now = [1000.0]
    source = [issue()]
    def select():
        return backlog.pick(kit.base, agent, fetcher=lambda _: source, clock=lambda: now[0])
    job, _ = select()
    backlog.finish(kit.base, job, agent, ("status", "Approval expired"), clock=lambda: now[0])
    now[0] += 1000
    assert select()[0] is None
    source[0] = issue(updatedAt="revision2")
    now[0] += backlog.POLL_S
    assert select()[0] is None


def test_assigned_and_blocked_issues_are_skipped_and_fetch_failures_are_visible(setup):
    kit, _, agent, _ = setup
    rows = [issue(1, assignees=[{"login": "someone"}]), issue(2, labels=[{"name": "blocked"}]), issue(3)]
    job, _ = backlog.pick(kit.base, agent, fetcher=lambda _: rows)
    assert job["number"] == 3
    def broken(_):
        raise RuntimeError("GitHub unavailable")
    _, detail = backlog.pick(kit.base, agent, fetcher=broken, clock=lambda: 10**12)
    assert "blocked" in detail and "GitHub unavailable" in detail


def pumped(monkeypatch, rows):
    """The issue source serves ``rows`` and the worker's loop is alive."""
    monkeypatch.setattr(backlog, "fetch", lambda _: rows)
    monkeypatch.setattr(la, "alive", lambda _: True)
    original = backlog.pick
    monkeypatch.setattr(backlog, "pick", lambda base, a, **kw: original(base, a, fetcher=backlog.fetch, **kw))


def test_idle_dispatch_creates_task_and_notifies_worker_in_its_inbox(setup, monkeypatch):
    kit, parent, agent, project = setup
    pumped(monkeypatch, [issue()])
    la.Status(kit.base, agent.name).update(state="idle")
    issuepump.step(parent, kit.base, agent.name)
    tasks = Tasks(parent).list()['tasks']
    assert len(tasks) == 1 and tasks[0]['created_by'] == kit.lead.name
    assert str(project) not in tasks[0]['description'] and 'bare pytest' in tasks[0]['description']
    assert tasks[0]['source_key'] == 'github:sample/project:1:revision1'
    assert tasks[0]['capabilities'] == ['coding'] and tasks[0]['limits']['max_retries'] == 0
    Tasks(kit.person).subscribe(tasks[0]['id'])
    assert sorted(Tasks(parent).watchers(tasks[0]['id'])) == sorted([agent.identity, kit.person.name])
    notices = inbox(kit, agent)
    assert [e.fields['type'] for e in notices[:2]] == ['task', 'status']
    assert 'Assigned project task' in notices[0].text and f'{kit.person.name} subscribed task' in notices[1].text
    assert all(tasks[0]['id'] in e.text for e in notices)
    issuepump.step(parent, kit.base, agent.name)
    assert len(Tasks(parent).list()['tasks']) == 1
    kit.session(kit.node.adopt(agent.identity)).post(kit.lead.name, 'answer', 'Discussion claiming the patch is done')
    issuepump.step(parent, kit.base, agent.name)
    assert Tasks(parent).get(tasks[0]['id'])['state'] == 'queued'
    Tasks(kit.person).unsubscribe(tasks[0]['id'])
    assert Tasks(parent).watchers(tasks[0]['id']) == [agent.identity]


def test_producer_keeps_ahead_of_the_worker_with_a_durable_queue(setup, monkeypatch):
    kit, parent, agent, _ = setup
    pumped(monkeypatch, [issue(n) for n in range(1, 6)])
    la.Status(kit.base, agent.name).update(state="working")
    issuepump.step(parent, kit.base, agent.name)
    tasks = Tasks(parent).list()['tasks']
    assert len(tasks) == issuepump.QUEUE_DEPTH
    notices = inbox(kit, agent)
    assert sum(e.fields['type'] == 'task' for e in notices) == issuepump.QUEUE_DEPTH
    monkeypatch.setattr(backlog, 'pid_exists', lambda _: False)
    issuepump.step(parent, kit.base, agent.name)
    assert len(Tasks(parent).list()['tasks']) == issuepump.QUEUE_DEPTH
    assert len(inbox(kit, agent)) == len(notices)
    assert all(task['state'] == 'queued' for task in tasks)


def test_interrupted_projection_preserves_task_without_duplicate_dispatch(setup, monkeypatch):
    kit, parent, agent, _ = setup
    pumped(monkeypatch, [issue()])
    la.Status(kit.base, agent.name).update(state="idle")
    previous = issuepump._status
    def interrupted(*args, **kwargs):
        raise RuntimeError('Projection interrupted after commit')
    monkeypatch.setattr(issuepump, '_status', interrupted)
    with pytest.raises(RuntimeError, match='Projection interrupted'):
        issuepump.step(parent, kit.base, agent.name)
    monkeypatch.setattr(issuepump, '_status', previous)
    issuepump.step(parent, kit.base, agent.name)
    assert len(Tasks(parent).list()['tasks']) == 1
    assert sum(e.fields['type'] == 'task' for e in inbox(kit, agent)) == 1


def test_parent_superseded_issue_stays_excluded_after_revision_change(setup):
    kit, parent, agent, _ = setup
    record = backlog.supersede(parent, kit.base, agent.name, 6, 'Owner requires Python >=3.12; older floor obsolete')
    assert record['state'] == 'superseded' and record['decision_by'] == kit.lead.name
    assert record['summary'] == 'Owner requires Python >=3.12; older floor obsolete' and record['decision_at']
    job, _ = backlog.pick(kit.base, agent, fetcher=lambda _: [issue(6, updatedAt='new-revision'), issue(8)])
    assert job['number'] == 8
    assert record_of(kit, 'issue:sample/project:6')['decision_by'] == kit.lead.name


def test_child_and_foreign_parent_cannot_supersede_repository_tasks(setup):
    kit, _, agent, _ = setup
    for who in (kit.node.adopt(agent.identity), kit.stranger):
        with pytest.raises(Denied, match='registered worker parent'):
            backlog.supersede(kit.session(who), kit.base, agent.name, 6, 'Ignore the current request')
    assert backlog.edited(kit.base, lambda ledger: ledger.get('records', {})) == {}


def test_maintained_cli_records_parent_decision_without_person_impersonation(setup):
    kit, _, agent, _ = setup
    args = SimpleNamespace(action='supersede-issue', target=agent.name, agent=kit.lead.name, token_file='',
                           board=kit.node.board, issue=6, reason='Owner raised the minimum interpreter version')
    assert localcli.run(args, kit.base) == 0
    assert backlog.pick(kit.base, agent, fetcher=lambda _: [issue(6)])[0] is None
    assert record_of(kit, 'issue:sample/project:6')['decision_by'] == kit.lead.name


def test_parent_resume_preserves_failed_attempt_and_consumes_one_retry(setup):
    kit, parent, agent, _ = setup
    first, _ = backlog.pick(kit.base, agent, fetcher=lambda _: [issue(8)])
    backlog.finish(kit.base, first, agent, ('status', 'Legacy approval expired'))
    resumed = backlog.resume(parent, kit.base, agent.name, 8, 'Runtime replaces legacy execution')
    assert resumed['failures'] == 1 and resumed['retry_budget'] == 1
    assert resumed['state'] == 'ready' and resumed['summary'] == 'Legacy approval expired'
    assert resumed['resume_by'] == kit.lead.name and resumed['resume_reason'] == 'Runtime replaces legacy execution'
    assert resumed['prior_attempt'].startswith('issue-attempt:') and resumed['owner'] == agent.identity
    second, _ = backlog.pick(kit.base, agent, fetcher=lambda _: [issue(8)])
    assert second['number'] == 8
    assert record_of(kit, second['key'])['retry_budget'] == 0
    backlog.finish(kit.base, second, agent, ('status', 'Different blocked condition'))
    assert backlog.pick(kit.base, agent, fetcher=lambda _: [issue(8)])[0] is None


def test_issue_resume_refuses_unblocked_foreign_and_child_requests(setup):
    kit, parent, agent, _ = setup
    with pytest.raises(ValueError, match='explicitly blocked'):
        backlog.resume(parent, kit.base, agent.name, 8, 'Changed source')
    job, _ = backlog.pick(kit.base, agent, fetcher=lambda _: [issue(8)])
    backlog.finish(kit.base, job, agent, ('status', 'Blocked'))
    for who in (kit.node.adopt(agent.identity), kit.stranger):
        with pytest.raises(Denied, match='registered worker parent'):
            backlog.resume(kit.session(who), kit.base, agent.name, 8, 'Changed source')
    assert record_of(kit, job['key'])['state'] == 'blocked'


def test_issue_resume_cannot_bypass_task_retry_budget(setup):
    kit, parent, agent, _ = setup
    job, _ = backlog.pick(kit.base, agent, fetcher=lambda _: [issue(8)])
    backlog.finish(kit.base, job, agent, ('status', 'Blocked task'))
    backlog.edited(kit.base, lambda ledger: ledger.setdefault('dispatch', {}).__setitem__('task-1', {'task': 'task-1', 'worker': agent.identity, 'issue': job}))
    with pytest.raises(Denied, match='existing retry budget'):
        backlog.resume(parent, kit.base, agent.name, 8, 'Override the failure cap')

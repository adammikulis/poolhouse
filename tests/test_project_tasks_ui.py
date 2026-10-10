"""Human-authorized task controls use the selected project store."""

import pytest
import test_board_ui as board_ui

from poolhouse.workspace import tokens
from poolhouse.workspace.taskboard import TaskBoard

board_for = board_ui.board_for
PROJECT = board_ui.PROJECT
OTHER = board_ui.OTHER


def task_call(board_for, project=PROJECT, **options):
    call, _, projects = board_for
    projects[project].name = 'Experiment' if project == PROJECT else 'Other experiment'
    return call('', project=project, path=f'/ui/projects/{project}/tasks', **options)


def test_tasks_preserve_records_and_ignore_unrelated_default_coordinator(board_for, monkeypatch):
    from poolhouse.workspace import coordinator_config

    monkeypatch.setattr(coordinator_config, 'load', lambda _: {'mode': 'remote'})
    _, workspaces, _ = board_for
    ws = workspaces[PROJECT]
    owner = tokens.read_file(tokens.directory(ws.base) / tokens.OWNER_FILE)
    existing = TaskBoard(ws).create(owner, {'title': 'Existing task', 'acceptance': ['Reviewed']})
    status, created = task_call(board_for, method='POST', body={'spec': {
        'title': 'New task', 'acceptance': ['Passed'], 'deps': [existing['id']]}})
    assert status == 200, created
    assert created['project'] == {'key': PROJECT, 'name': 'Experiment'}
    status, result = task_call(board_for)
    assert status == 200, result
    assert {task['id'] for task in result['tasks']} == {existing['id'], created['id']}
    assert result['overview']['remaining'] == 2 and result['overview']['queued'] == 2
    assert result['overview']['eta_seconds'] is None
    status, other = task_call(board_for, OTHER)
    assert status == 200 and other['tasks'] == []
    assert workspaces[OTHER].base != ws.base


@pytest.mark.parametrize('options', [{'cookie': 'forged'}, {'ip': '198.51.100.4'},
                                    {'method': 'POST', 'origin': 'https://outside.invalid',
                                     'body': {'spec': {'title': 'Forbidden', 'acceptance': ['No']}}}])
def test_tasks_reject_forged_remote_and_cross_origin_people(board_for, options):
    status, _ = task_call(board_for, **options)
    assert status == 403


def test_tasks_refuse_foreign_project_metadata_and_agent_owner(board_for):
    status, _ = task_call(board_for, method='POST', body={'spec': {
        'title': 'Wrong project', 'acceptance': ['No'], 'project': {'key': OTHER}}})
    assert status == 403
    _, workspaces, projects = board_for
    ws = workspaces[PROJECT]
    owner = tokens.read_file(tokens.directory(ws.base) / tokens.OWNER_FILE)
    tokens.store(ws.base, tokens.OWNER_FILE, ws.mint(owner, 'another-worker'))
    # the person is the live browser session bound to this project, never the owner file's token
    assert task_call(board_for)[0] == 200
    agents = ws.registry._load()
    person = next(name for name, entry in agents.items() if entry['role'] == 'human')
    agents[person]['person_project'] = OTHER
    ws.registry._save(agents)
    assert task_call(board_for)[0] == 403
    assert task_call(board_for, method='POST', body={'spec': {'title': 'Unbound', 'acceptance': ['No']}})[0] == 403
    projects[OTHER].board_host = 'http://foreign:8770'
    assert task_call(board_for, OTHER)[0] == 409

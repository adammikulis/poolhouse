"""Human-authorized task controls use the selected project's board on the node."""

import pytest
from person_kit import OTHER, PROJECT

from poolhouse.board import person
from poolhouse.board.tasks import Tasks

pytest_plugins = ["node_kit", "person_kit"]


def board_of(node, projects, ident):
    """The person's session on the board of one project."""
    return person.seat(projects[ident].root, node.client)


def test_tasks_preserve_records_and_show_those_made_through_the_node(project_tasks):
    call, node, projects = project_tasks
    lead = board_of(node, projects, PROJECT)
    existing = Tasks(lead).create({'title': 'Existing task', 'acceptance': ['Reviewed']})
    status, created = call(method='POST', body={'spec': {
        'title': 'New task', 'acceptance': ['Passed'], 'deps': [existing['id']]}})
    assert status == 200, created
    assert created['project'] == {'key': PROJECT, 'name': 'experiment'}
    status, result = call()
    assert status == 200, result
    assert {task['id'] for task in result['tasks']} == {existing['id'], created['id']}
    assert result['overview']['remaining'] == 2 and result['overview']['queued'] == 2
    assert result['overview']['eta_seconds'] is None
    status, other = call(project=OTHER)
    assert status == 200 and other['tasks'] == []
    assert board_of(node, projects, OTHER).board != lead.board


@pytest.mark.parametrize('options', [{'cookie': 'forged'}, {'ip': '198.51.100.4'},
                                    {'method': 'POST', 'origin': 'https://outside.invalid',
                                     'body': {'spec': {'title': 'Forbidden', 'acceptance': ['No']}}}])
def test_tasks_reject_forged_remote_and_cross_origin_people(project_tasks, options):
    call, node, projects = project_tasks
    assert call(**options)[0] == 403
    assert Tasks(board_of(node, projects, PROJECT)).list()['tasks'] == []


def test_tasks_refuse_foreign_project_metadata(project_tasks):
    call, node, projects = project_tasks
    status, _ = call(method='POST', body={'spec': {
        'title': 'Wrong project', 'acceptance': ['No'], 'project': {'key': OTHER}}})
    assert status == 403
    assert Tasks(board_of(node, projects, PROJECT)).list()['tasks'] == []


def test_person_task_create_names_the_project(project_tasks):
    call, _, _ = project_tasks
    status, task = call(method='POST', body={'spec': {'title': 'Replay', 'acceptance': ['Replay passes']}})
    assert status == 200 and task['project']['key'] == PROJECT


def test_a_directory_that_is_no_project_is_refused_not_given_another_projects_tasks(project_tasks, tmp_path):
    call, _, projects = project_tasks
    projects[PROJECT].root = str(tmp_path)
    assert call()[0] == 403

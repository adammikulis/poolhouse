"""Read-only preflight listing what strict enforcement would stop on a board or in a workspace project."""

from __future__ import annotations

from typing import Any

from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks
from poolhouse.workspace import enforcement
from poolhouse.workspace.identity import HUMAN
from poolhouse.workspace.taskboard import TaskBoard

LIVE = ('queued', 'working', 'review', 'blocked')


def _note(found: list[dict[str, str]], task: dict[str, Any], kind: str, detail: str) -> None:
    found.append({'task': task['id'], 'title': task['title'], 'state': task['state'], 'kind': kind, 'detail': detail})


def check(s: Session) -> dict[str, Any]:
    """Tasks of the board that strict mode would strand, each with the reason."""
    listed = Tasks(s).list()
    agents = {a.name: a for a in s.agents()}
    found: list[dict[str, str]] = []

    def top(name: str) -> bool:
        return name in agents and not agents[name].parent

    for task in listed['tasks']:
        if task['state'] == 'queued' and not task['assignees'] and not top(task['created_by']):
            _note(found, task, 'unassigned',
                  f'created by {task["created_by"]}; only its creator\'s own workers could claim it')
        if task['state'] in LIVE:
            for identity in task['assignees'] + task['reviewers']:
                if identity not in agents or agents[identity].retired:
                    _note(found, task, 'designated', f'{identity} is not a live session of this board')
        review = task.get('review')
        if task['state'] == 'accepted' and review:
            verifier = review['verifier']
            parent = agents[task['worker']].parent if task['worker'] in agents else ''
            if not (top(verifier) or verifier in task['reviewers'] or verifier == task['created_by'] == parent):
                _note(found, task, 'review', f'reviewer {verifier} would not qualify for a new review')
    return {'board': s.board, 'mode': listed['mode'], 'tasks': len(listed['tasks']), 'stranded': found, 'clear': not found}


def check_project(ws, key: str) -> dict[str, Any]:
    """Tasks of workspace project ``key`` that strict mode would strand, each with the reason."""
    board = TaskBoard(ws)
    with board._store() as graph:
        tasks = [node['attrs'] for node in graph.nodes('task')
                 if enforcement.key_of(node['attrs']['project']) == key]
        verifiers = {node['id']: node['attrs']['verifier'] for node in graph.nodes('review')}
    found: list[dict[str, str]] = []
    for task in tasks:
        if task['state'] == 'queued' and not task['assignees'] and ws.registry.role_of(task['created_by']) != HUMAN:
            _note(found, task, 'unassigned',
                  f'created by {task["created_by"]}; only its creator\'s own workers could claim it')
        if task['state'] in LIVE:
            for identity in task['assignees'] + task['reviewers']:
                if not board._project_grant(identity, task, 'strict'):
                    _note(found, task, 'designated', f'{identity} is not registered in the task project')
        if task['state'] == 'accepted' and task.get('review_id') in verifiers:
            verifier = verifiers[task['review_id']]
            if not board._qualifies(verifier, ws.registry.role_of(verifier), task, 'strict'):
                _note(found, task, 'review', f'reviewer {verifier} would not qualify for a new review')
    return {'project': key, 'mode': enforcement.current(ws, key)['mode'], 'tasks': len(tasks),
            'stranded': found, 'clear': not found}

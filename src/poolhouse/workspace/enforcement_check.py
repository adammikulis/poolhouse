"""Read-only preflight listing what strict enforcement would stop on a board."""

from __future__ import annotations

from typing import Any

from poolhouse.board.session import Session
from poolhouse.board.tasks import Tasks

LIVE = ('queued', 'working', 'review', 'blocked')


def check(s: Session) -> dict[str, Any]:
    """Tasks of the board that strict mode would strand, each with the reason."""
    listed = Tasks(s).list()
    agents = {a.name: a for a in s.agents()}
    found: list[dict[str, str]] = []

    def note(task: dict[str, Any], kind: str, detail: str) -> None:
        found.append({'task': task['id'], 'title': task['title'], 'state': task['state'], 'kind': kind, 'detail': detail})

    def top(name: str) -> bool:
        return name in agents and not agents[name].parent

    for task in listed['tasks']:
        if task['state'] == 'queued' and not task['assignees'] and not top(task['created_by']):
            note(task, 'unassigned', f'created by {task["created_by"]}; only its creator\'s own workers could claim it')
        if task['state'] in LIVE:
            for identity in task['assignees'] + task['reviewers']:
                if identity not in agents or agents[identity].retired:
                    note(task, 'designated', f'{identity} is not a live session of this board')
        review = task.get('review')
        if task['state'] == 'accepted' and review:
            verifier = review['verifier']
            parent = agents[task['worker']].parent if task['worker'] in agents else ''
            if not (top(verifier) or verifier in task['reviewers'] or verifier == task['created_by'] == parent):
                note(task, 'review', f'reviewer {verifier} would not qualify for a new review')
    return {'board': s.board, 'mode': listed['mode'], 'tasks': len(listed['tasks']), 'stranded': found, 'clear': not found}

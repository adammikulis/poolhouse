"""The person's Tasks page: the board's tasks read and written through the person's session on the node."""

import json
from pathlib import Path

from poolhouse.board import client, person
from poolhouse.board.tasks import Tasks
from poolhouse.fleet.request_fields import object_body
from poolhouse.fleet.same_page import checked
from poolhouse.workspace import task_outcomes


def _create(tasks, body, project):
    spec = body.get('spec')
    if not isinstance(spec, dict):
        raise ValueError('a task specification is required')
    scope = spec.get('project', {})
    if project is not None:
        if not isinstance(scope, dict) or scope.get('key', project['key']) != project['key']:
            raise client.Denied('denied', 'the task belongs to another project')
        spec = {key: value for key, value in spec.items() if key != 'project'}
    made = tasks.create(spec)
    return {**made, 'project': project} if project is not None else made


def _post(seat, body, project):
    tasks = Tasks(seat)
    action = body.get('action', 'create')
    if action == 'create' and set(body) <= {'action', 'spec'}:
        return _create(tasks, body, project)
    if action == 'review' and set(body) == {'action', 'id', 'decision'}:
        return task_outcomes.seat_review(seat, body['id'], body['decision'])
    if action == 'credit' and set(body) == {'action', 'id'}:
        return {'credit': task_outcomes.seat_credit(seat, body['id'])}
    if action in ('resume', 'recover') and set(body) == {'action', 'id', 'reason'}:
        return getattr(tasks, action)(body['id'], body['reason'])
    raise ValueError('tasks accept create, independent review, resume or recovery requests')


def route(request, *, prefix="/ui/tasks", root=None, project=None) -> bool:
    """Answer one request of the Tasks page for the board of the project at ``root`` (the working directory when none)."""
    if request.path != prefix:
        return False
    if request.method not in ('GET', 'POST'):
        request.send(405, {'error': 'tasks support GET and POST'})
        return True
    headers = {key.lower(): value for key, value in request.handler.headers.items()}
    refused = checked(request.method, headers, request.handler.server.server_address[1], writes=True)
    if refused:
        request.send(refused[0], json.loads(refused[2]))
        return True
    try:
        seat = person.seat(Path(root) if root else Path.cwd())
        if request.method == 'GET':
            ident = request.asked('id', '')
            result = Tasks(seat).get(ident) if ident else Tasks(seat).list()
        else:
            result = _post(seat, object_body(request), project)
        request.send(200, result)
    except client.Denied as error:
        request.send(403, {'error': str(error)})
    except (ValueError, UnicodeError, client.Invalid) as error:
        request.send(400, {'error': str(error)})
    except (OSError, RuntimeError, client.NodeError):
        request.send(503, {'error': 'task coordination is unavailable'})
    return True

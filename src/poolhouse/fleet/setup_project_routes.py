"""Local credentialed setup choices and holder-only coding-folder callbacks."""
from __future__ import annotations

import re

from poolhouse.studio.store import digest

from . import invite_routes
from .launch_secret import HEADER
from .request_fields import exact_body
from .session import parse_cookie
from .setup_project_picker import Picker
from .setup_projects import Projects
from .studio_service import scope

PREFIX = '/ui/setup/projects'
NATIVE = PREFIX + '/native-picks/'
ERROR = 'This folder selection ended. Choose the folder again.'


class Service:
    def __init__(self, ui):
        self.ui, self.bindings = ui, {}
        self.projects = Projects(ui.root, ui.projects)
        self.picker = Picker(self.projects, epoch=lambda principal: principal['scope'], current=self.current)

    def bind(self, route):
        ui = self.ui
        session = ui.sessions.get(parse_cookie(route.cookie))
        if session is None or not session.credentialed or not route._local_browser():
            raise PermissionError('Open setup from the Poolhouse window.')
        bound = scope(ui)
        principal = {'id': digest({'session': session.sid}), 'scope': bound, 'owner': True}
        self.bindings[(principal['id'], bound)] = lambda: (ui.sessions.get(session.sid) is session
                                                         and session.credentialed and scope(ui) == bound)
        return principal

    def current(self, principal, epoch):
        callback = self.bindings.get((principal['id'], principal['scope']))
        return principal['scope'] == epoch and callback is not None and callback() is True


def service(ui):
    with ui._setup_jobs_lock:
        result = getattr(ui, 'setup_projects', None)
        if result is None:
            if ui.root is None or ui.projects is None:
                raise ValueError('Coding folders are unavailable on this device.')
            result = ui.setup_projects = Service(ui)
        return result


def public(route):
    """Native possession proof does not bypass the original renderer's current session."""
    if not route.path.startswith(NATIVE):
        return False
    match = re.fullmatch(re.escape(NATIVE) + r'([a-f0-9]{32})/(claim|complete|cancel)', route.path)
    if match is None:
        route.send(404, {'error': 'No such folder selection'})
        return True
    if route.method != 'POST':
        route.send(405, {'error': 'POST required'})
        return True
    ui = route.ui
    if (ui.launch is None or not invite_routes.local(route.client_ip) or not ui.host_ok(route.host_header)
            or route.header('Origin') or route.header('Sec-Fetch-Site')
            or not ui.launch.matches(route.header(HEADER))):
        route.send(403, {'error': 'Choose a folder from the Poolhouse window.'})
        return True
    ident, action = match.groups()
    try:
        fields = {'claim': ('nonce',), 'complete': ('nonce', 'claim', 'path'), 'cancel': ('nonce', 'claim')}[action]
        body = exact_body(route, fields)
        instance = getattr(ui, 'setup_projects', None)
        if instance is None:
            raise PermissionError(ERROR)
        response = getattr(instance.picker, action)(ident, **body)
        route.send(200, response, {'Cache-Control': 'no-store'})
    except PermissionError:
        route.send(403, {'error': ERROR})
    except (ValueError, OSError, KeyError, RuntimeError):
        route.send(409, {'error': ERROR})
    return True


def route(context):
    if context.path not in {PREFIX + '/pick', PREFIX + '/default', PREFIX + '/create'}:
        return False
    if context.method != 'POST':
        context.send(405, {'error': 'POST required'})
        return True
    try:
        instance = service(context.ui)
        principal = instance.bind(context)
        body = exact_body(context, ('candidate_id',) if context.path.endswith('/create') else ())
        if context.path.endswith('/pick'):
            response = instance.picker.request(principal)
        elif context.path.endswith('/default'):
            response = {'project': instance.projects.default(current=lambda: instance.current(principal, principal['scope']))}
        else:
            candidate = body['candidate_id']
            if not isinstance(candidate, str) or not re.fullmatch(r'[a-f0-9]{32}', candidate):
                raise ValueError(ERROR)
            response = instance.picker.create(principal, candidate)
        context.send(200, {'ok': True, **response}, {'Cache-Control': 'no-store'})
    except PermissionError:
        context.send(403, {'error': ERROR})
    except (ValueError, OSError, KeyError, RuntimeError):
        context.send(409, {'error': 'The coding folder could not be prepared. Choose a folder again.'})
    return True

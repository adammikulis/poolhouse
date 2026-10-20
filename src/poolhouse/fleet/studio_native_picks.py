"""One-use native selections; resource authority belongs to the injected source registry."""
from __future__ import annotations

import json
import re

from poolhouse.json_objects import unique_object
from poolhouse.studio.store import Conflict

from . import invite_routes
from .launch_secret import HEADER

NATIVE = '/ui/studio/native-picks/'
ERROR = 'This file selection ended. Choose the source again in Studio.'



def _body(context, keys):
    length = int(context.header('Content-Length', '0') or 0)
    if not 0 <= length <= 64 * 1024:
        raise ValueError('Invalid selection request size')
    body = json.loads(context.handler.rfile.read(length) or b'{}', object_pairs_hook=unique_object)
    if not isinstance(body, dict) or set(body) != set(keys):
        raise ValueError('Invalid selection fields')
    return body


class NativePickRoutes:
    """Only exact native callbacks get the launch-secret guard; ordinary Studio remains guarded."""

    def public_route(self):
        if not self.path.startswith(NATIVE):
            return super().public_route()
        match = re.fullmatch(re.escape(NATIVE) + r'([a-f0-9]{32})/(begin|complete|cancel)', self.path)
        if not match:
            self.send(404, {'error': 'No such native selection'})
            return True
        if self.method != 'POST':
            self.send(405, {'error': 'POST required'})
            return True
        ui = self.ui
        if (ui.launch is None or not invite_routes.local(self.client_ip) or not ui.host_ok(self.host_header)
                or self.header('Origin') or self.header('Sec-Fetch-Site') or not ui.launch.matches(self.header(HEADER))):
            self.send(403, {'error': 'Use the native app to choose an original source'})
            return True
        picks = getattr(ui, 'studio_native_picks', None)
        if picks is None:
            self.send(409, {'error': 'Original source selection is not available yet'})
            return True
        ident, action = match.groups()
        keys = {'begin': ('nonce',), 'cancel': ('nonce', 'claim'), 'complete': ('nonce', 'claim', 'path')}[action]
        try:
            request = _body(self, keys)
            response = getattr(picks, action)(ident, **request)
            self.send(200, response, {'Cache-Control': 'no-store'})
        except PermissionError:
            self.send(403, {'error': ERROR})
        except (ValueError, OSError, KeyError, Conflict):
            self.send(409, {'error': ERROR})
        return True

    def route(self):
        if self.path != '/ui/studio/sources/pick':
            return super().route()
        if self.method != 'POST':
            self.send(405, {'error': 'POST required'})
            return True
        picks = getattr(self.ui, 'studio_native_picks', None)
        if picks is None:
            self.send(409, {'error': 'Original source selection is not available yet'})
            return True
        try:
            engine, principal = self._service()
            if not engine.authorize(principal, None, {}):
                raise PermissionError(ERROR)
            request = _body(self, ('mode', 'request_key'))
            self.send(200, picks.request(principal, **request), {'Cache-Control': 'no-store'})
        except PermissionError:
            self.send(403, {'error': ERROR})
        except (ValueError, OSError, KeyError, Conflict):
            self.send(409, {'error': ERROR})
        return True
